import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

import video_artifact
from video_artifact import build_video_artifact, extract_key_frames, plain_text


def make_silent_video(path: Path, seconds: int = 6) -> None:
    """造一段纯画面无音频的视频（testsrc 有画面，无音轨）。"""
    result = subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", f"testsrc=size=320x240:rate=10:duration={seconds}",
         "-pix_fmt", "yuv420p", str(path)],
        capture_output=True,
    )
    if result.returncode:
        raise unittest.SkipTest("ffmpeg 不可用，跳过")


def make_silent_video_with_audio(path: Path, seconds: int = 6) -> None:
    """有音轨但内容为静音的视频。"""
    result = subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", f"testsrc=size=320x240:rate=10:duration={seconds}",
         "-f", "lavfi", "-i", f"anullsrc=r=44100:cl=mono:d={seconds}",
         "-pix_fmt", "yuv420p", "-shortest", str(path)],
        capture_output=True,
    )
    if result.returncode:
        raise unittest.SkipTest("ffmpeg 不可用，跳过")


class PlainTextTest(unittest.TestCase):
    def test_plain_text_strips_index_and_timestamps(self):
        srt = "1\n00:00:00,000 --> 00:00:01,000\n你好\n\n2\n00:00:01,000 --> 00:00:02,000\n世界\n"
        self.assertEqual(plain_text(srt), "你好 世界")

    def test_plain_text_of_empty_srt(self):
        self.assertEqual(plain_text(""), "")


class KeyFramesTest(unittest.TestCase):
    def test_extract_key_frames_from_silent_video(self):
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            video = root / "silent.mp4"
            make_silent_video(video, seconds=6)
            frames = extract_key_frames(video, root / "images", interval=1.0, max_frames=6)
            self.assertTrue(frames)
            self.assertTrue(all(f.suffix == ".webp" for f in frames))
            self.assertTrue(all(f.stat().st_size > 0 for f in frames))
            # 6 秒 / 每秒 1 帧 = 6 帧。少于此说明编码器把多帧并成了一个文件
            # （按扩展名会选 libwebp_anim，动画 webp 只有一个文件）
            self.assertEqual(len(frames), 6)

    def test_key_frames_are_separate_files_with_expected_size(self):
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            video = root / "silent.mp4"
            make_silent_video(video, seconds=4)
            frames = extract_key_frames(video, root / "images", interval=1.0, max_frames=8, width=540)
            self.assertEqual(len(frames), 4)
            for frame in frames:
                probe = subprocess.run(
                    ["ffprobe", "-v", "error", "-select_streams", "v:0",
                     "-show_entries", "stream=width,height,codec_name", "-of", "csv=p=0", str(frame)],
                    capture_output=True,
                )
                codec, width, height = probe.stdout.decode().strip().split(",")
                self.assertEqual(codec, "webp")
                self.assertEqual(int(width), 320)  # 源视频 320 宽，不应被放大
                self.assertEqual(int(height), 240)

    def test_has_audio_stream_detects_missing_and_present(self):
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            no_audio = root / "a.mp4"
            make_silent_video(no_audio)
            self.assertFalse(video_artifact.has_audio_stream(no_audio))
            with_audio = root / "b.mp4"
            make_silent_video_with_audio(with_audio)
            self.assertTrue(video_artifact.has_audio_stream(with_audio))


class BuildVideoArtifactTest(unittest.TestCase):
    def test_speech_produces_transcript(self):
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            video = root / "v.mp4"
            make_silent_video_with_audio(video, seconds=2)
            with patch.object(video_artifact, "transcribe_video", return_value="1\n00:00:00,000 --> 00:00:01,000\n说话\n"):
                body, extra = build_video_artifact(video, root / "images", root)
            self.assertIn("说话", body)
            self.assertEqual(extra["transcript_model"], "small")
            self.assertNotIn("frame_count", extra)

    def test_no_speech_falls_back_to_key_frames(self):
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            video = root / "v.mp4"
            make_silent_video(video, seconds=4)
            with patch.object(video_artifact, "transcribe_video", return_value=""):
                body, extra = build_video_artifact(video, root / "images", root, interval=1.0, max_frames=4)
            self.assertIn("未识别到语音", body)
            self.assertIn("![帧 1]", body)
            self.assertEqual(extra["speech"], "none")
            self.assertGreaterEqual(extra["frame_count"], 1)
            self.assertTrue((root / "images").is_dir())
            self.assertTrue(list((root / "images").glob("*.webp")))

    def test_no_audio_track_skips_asr_entirely(self):
        """没有音频轨的视频不能进 ASR —— ffmpeg 抽音频那步就会失败。"""
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            video = root / "v.mp4"
            make_silent_video(video, seconds=4)
            with patch.object(video_artifact, "transcribe_video") as transcribe:
                body, extra = build_video_artifact(video, root / "images", root, interval=1.0, max_frames=4)
            transcribe.assert_not_called()
            self.assertIn("无音频轨", body)
            self.assertIn("![帧 1]", body)
            self.assertEqual(extra["speech"], "none")

    def test_empty_transcription_with_audio_track_is_still_flagged(self):
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            video = root / "v.mp4"
            make_silent_video_with_audio(video, seconds=4)
            with patch.object(video_artifact, "transcribe_video", return_value="   \n"):
                body, extra = build_video_artifact(video, root / "images", root, interval=1.0, max_frames=4)
            self.assertIn("有音频轨但未识别出内容", body)
            self.assertEqual(extra["speech"], "none")


if __name__ == "__main__":
    unittest.main()
