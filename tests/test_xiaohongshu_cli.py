import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

import video_artifact
import xiaohongshu_cli


VIDEO_URL = "https://www.xiaohongshu.com/explore/6aabba200000000025034beb?xsec_token=secret"
IMAGE_URL = "https://www.xiaohongshu.com/explore/6aa540d0000000002902d565?xsec_token=secret"


def _write_tiny_video(path: Path, with_audio: bool = True) -> None:
    """造一段 1 秒的真视频，ffprobe/ffmpeg 能正常读。"""
    import subprocess

    cmd = ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=5:duration=1"]
    if with_audio:
        cmd += ["-f", "lavfi", "-i", "anullsrc=r=8000:cl=mono:d=1"]
    cmd += ["-pix_fmt", "yuv420p"]
    if with_audio:
        cmd += ["-shortest"]
    cmd.append(str(path))
    subprocess.run(cmd, capture_output=True, check=True)


class XiaohongshuCliTest(unittest.TestCase):
    def test_run_video_writes_transcript_and_keeps_no_media(self):
        def fake_discover(url, profile_dir):
            return ({"platform": "xiaohongshu", "note_id": "video1", "title": "视频", "note_type": "video"}, "https://cdn/video.mp4", [])

        def fake_download_video_asset(video_url, note, scratch):
            scratch.mkdir(parents=True, exist_ok=True)
            path = scratch / "video.mp4"
            _write_tiny_video(path, with_audio=True)
            return path

        with tempfile.TemporaryDirectory() as directory:
            scratch = Path(directory) / "scratch"
            with patch.object(xiaohongshu_cli, "discover_note", side_effect=fake_discover):
                with patch.object(xiaohongshu_cli, "download_video_asset", side_effect=fake_download_video_asset):
                    with patch.object(xiaohongshu_cli, "scratch_dir", return_value=scratch):
                        with patch.object(video_artifact, "transcribe_video", return_value="1\n00:00:00,000 --> 00:00:01,000\n字幕\n"):
                            output = xiaohongshu_cli.run(VIDEO_URL, Path(directory))

            content = output.read_text(encoding="utf-8")
            self.assertEqual(output.name, "video1.md")
            self.assertIn("note_type: video", content)
            self.assertIn("transcript_model: small", content)
            # 正文是转录文本
            self.assertIn("00:00:00,000", content)
            # 产物根里不留视频
            self.assertFalse((output.parent / "video.mp4").exists())
            # 暂存区的视频已删除
            self.assertFalse((scratch / "video.mp4").exists())

    def test_run_video_removes_media_when_transcription_fails(self):
        def fake_discover(url, profile_dir):
            return ({"platform": "xiaohongshu", "note_id": "video1", "title": "视频", "note_type": "video"}, "https://cdn/video.mp4", [])

        def fake_download_video_asset(video_url, note, scratch):
            scratch.mkdir(parents=True, exist_ok=True)
            path = scratch / "video.mp4"
            _write_tiny_video(path, with_audio=True)
            return path

        with tempfile.TemporaryDirectory() as directory:
            scratch = Path(directory) / "scratch"
            with patch.object(xiaohongshu_cli, "discover_note", side_effect=fake_discover):
                with patch.object(xiaohongshu_cli, "download_video_asset", side_effect=fake_download_video_asset):
                    with patch.object(xiaohongshu_cli, "scratch_dir", return_value=scratch):
                        with patch.object(video_artifact, "transcribe_video", side_effect=xiaohongshu_cli.VideoTranscriptionError("ASR_FAILED", "boom")):
                            with self.assertRaises(xiaohongshu_cli.XiaohongshuCliError):
                                xiaohongshu_cli.run(VIDEO_URL, Path(directory))
            # 失败也要清掉暂存的视频
            self.assertFalse((scratch / "video.mp4").exists())
            self.assertFalse((Path(directory) / "video1" / "video1.md").exists())

    def test_run_image_writes_all_image_paths(self):
        def fake_discover(url, profile_dir):
            return ({"platform": "xiaohongshu", "note_id": "image1", "title": "图文", "note_type": "image", "asset_count": 2}, None, ["https://cdn/01.webp", "https://cdn/02.webp"])

        def fake_download(note, image_urls, output_dir):
            image_dir = output_dir / "images"
            image_dir.mkdir(parents=True, exist_ok=True)
            for index in range(len(image_urls)):
                (image_dir / f"{index + 1:02d}.webp").write_bytes(b"image")
            return {"asset_count": 2, "asset_files": ["images/01.webp", "images/02.webp"]}

        with tempfile.TemporaryDirectory() as directory:
            with patch.object(xiaohongshu_cli, "discover_note", side_effect=fake_discover):
                with patch.object(xiaohongshu_cli, "download_assets", side_effect=fake_download):
                    output = xiaohongshu_cli.run(IMAGE_URL, Path(directory))
            content = output.read_text(encoding="utf-8")
            self.assertIn("note_type: image", content)
            self.assertIn("images/01.webp", content)
            self.assertTrue((output.parent / "images/02.webp").exists())
            # 正文要有 markdown 图片引用
            body = content.split("---\n", 2)[-1]
            self.assertIn("![图 1](images/01.webp)", body)
            self.assertIn("![图 2](images/02.webp)", body)

    def test_failed_asset_download_leaves_no_markdown(self):
        def fake_discover(url, profile_dir):
            return ({"platform": "xiaohongshu", "note_id": "broken", "note_type": "image", "asset_count": 1}, None, ["https://cdn/01.webp"])

        with tempfile.TemporaryDirectory() as directory:
            with patch.object(xiaohongshu_cli, "discover_note", side_effect=fake_discover):
                with patch.object(xiaohongshu_cli, "download_assets", side_effect=RuntimeError("download failed")):
                    with self.assertRaises(xiaohongshu_cli.XiaohongshuCliError):
                        xiaohongshu_cli.run(IMAGE_URL, Path(directory))
            self.assertFalse((Path(directory) / "broken" / "broken.md").exists())


if __name__ == "__main__":
    unittest.main()
