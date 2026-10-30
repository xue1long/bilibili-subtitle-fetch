import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

import douyin_cli
import video_artifact


URL = "https://www.douyin.com/user/self?modal_id=123456&showTab=favorite_collection"

VIDEO_POST = {
    "video_id": "123456",
    "media_kind": "video",
    "image_urls": [],
    "video_urls": ["https://cdn.invalid/target.mp4"],
    "payload": {
        "aweme_detail": {
            "aweme_id": "123456",
            "desc": "标题",
            "author": {"nickname": "作者"},
        }
    },
}

IMAGE_POST = {
    "video_id": "123456",
    "media_kind": "image",
    "image_urls": ["https://cdn.invalid/1.webp"],
    "video_urls": [],
    "payload": {
        "aweme_detail": {
            "aweme_id": "123456",
            "desc": "图文简介",
            "author": {"nickname": "作者"},
        }
    },
}


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


class DouyinCliTest(unittest.TestCase):
    def test_run_writes_metadata_before_srt(self):
        def fake_download(url, video_path, profile_dir, post=None):
            _write_tiny_video(video_path, with_audio=True)
            return {
                "platform": "douyin",
                "video_id": "123456",
                "title": "标题",
                "uploader": "作者",
                "description": "简介",
                "url": "https://www.douyin.com/video/123456",
            }

        with tempfile.TemporaryDirectory() as directory:
            scratch = Path(directory) / "scratch"
            with patch.object(douyin_cli, "resolve_post", return_value=VIDEO_POST):
                with patch.object(douyin_cli, "scratch_dir", return_value=scratch):
                    with patch.object(douyin_cli, "download_video", side_effect=fake_download):
                        with patch.object(video_artifact, "transcribe_video", return_value="1\n00:00:00,000 --> 00:00:01,000\n字幕\n"):
                            output = douyin_cli.run(URL, Path(directory), model_size="small")

            content = output.read_text(encoding="utf-8")
            self.assertEqual(output.name, "DY123456.md")
            self.assertLess(content.index("platform: douyin"), content.index("00:00:00,000"))
            self.assertIn("description: |", content)
            self.assertIn("transcript_model: small", content)
            # 视频不留在产物目录里
            self.assertFalse((output.parent / "DY123456.mp4").exists())
            # 暂存区用完即清空
            self.assertEqual(list(scratch.glob("**/*.mp4")), [])

    def test_failed_asr_does_not_leave_markdown(self):
        def fake_download(url, video_path, profile_dir, post=None):
            _write_tiny_video(video_path, with_audio=True)
            return {"video_id": "123456", "platform": "douyin"}

        with tempfile.TemporaryDirectory() as directory:
            scratch = Path(directory) / "scratch"
            with patch.object(douyin_cli, "resolve_post", return_value=VIDEO_POST):
                with patch.object(douyin_cli, "scratch_dir", return_value=scratch):
                    with patch.object(douyin_cli, "download_video", side_effect=fake_download):
                        with patch.object(video_artifact, "transcribe_video", side_effect=douyin_cli.VideoTranscriptionError("ASR_FAILED", "ASR failed")):
                            with self.assertRaises(douyin_cli.DouyinCliError):
                                douyin_cli.run(URL, Path(directory))
            self.assertFalse((Path(directory) / "DY123456.md").exists())
            # 转录失败也要清掉暂存的视频
            self.assertEqual(list(scratch.glob("**/*.mp4")), [])

    def test_image_post_writes_manifest_and_skips_transcription(self):
        def fake_download_images(urls, output_dir, referer=None):
            output_dir.mkdir(parents=True, exist_ok=True)
            (output_dir / "01.webp").write_bytes(b"image")
            return ["01.webp"]

        with tempfile.TemporaryDirectory() as directory:
            images_dir = Path(directory) / "05_抖音笔记图片"
            with patch.object(douyin_cli, "resolve_post", return_value=IMAGE_POST):
                with patch.object(douyin_cli, "download_images", side_effect=fake_download_images):
                    with patch.object(video_artifact, "transcribe_video") as transcribe:
                        output = douyin_cli.run(URL, Path(directory), image_output_dir=images_dir)

            transcribe.assert_not_called()
            self.assertEqual(output.name, "DY123456.md")
            self.assertEqual(output.parent, images_dir / "DY123456")
            self.assertTrue((output.parent / "01.webp").exists())
            content = output.read_text(encoding="utf-8")
            self.assertIn("note_type: image", content)
            self.assertIn("asset_count: 1", content)
            self.assertIn("- 01.webp", content)
            # 正文要有 markdown 图片引用，Obsidian 才能直接显示图
            body = content.split("---\n", 2)[-1]
            self.assertIn("![图 1](01.webp)", body)

    def test_image_post_failure_removes_partial_directory(self):
        def fake_download_images(urls, output_dir, referer=None):
            output_dir.mkdir(parents=True, exist_ok=True)
            (output_dir / "01.webp").write_bytes(b"image")
            raise douyin_cli.DouyinMediaError("ASSET_DOWNLOAD_FAILED", "boom")

        with tempfile.TemporaryDirectory() as directory:
            images_dir = Path(directory) / "05_抖音笔记图片"
            with patch.object(douyin_cli, "resolve_post", return_value=IMAGE_POST):
                with patch.object(douyin_cli, "download_images", side_effect=fake_download_images):
                    with self.assertRaises(douyin_cli.DouyinCliError):
                        douyin_cli.run(URL, Path(directory), image_output_dir=images_dir)
            self.assertFalse((images_dir / "DY123456").exists())


if __name__ == "__main__":
    unittest.main()
