import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

import xiaohongshu_cli


VIDEO_URL = "https://www.xiaohongshu.com/explore/6aabba200000000025034beb?xsec_token=secret"
IMAGE_URL = "https://www.xiaohongshu.com/explore/6aa540d0000000002902d565?xsec_token=secret"


class XiaohongshuCliTest(unittest.TestCase):
    def test_run_video_writes_metadata_and_video(self):
        def fake_discover(url, profile_dir):
            return ({"platform": "xiaohongshu", "note_id": "video1", "title": "视频", "note_type": "video", "asset_count": 1}, "https://cdn/video.mp4", [])

        def fake_download(note, video_url, image_urls, output_dir):
            output_dir.mkdir(parents=True, exist_ok=True)
            (output_dir / "video.mp4").write_bytes(b"video")
            return {"asset_count": 1, "asset_files": ["video.mp4"]}

        with tempfile.TemporaryDirectory() as directory:
            with patch.object(xiaohongshu_cli, "discover_note", side_effect=fake_discover):
                with patch.object(xiaohongshu_cli, "download_assets", side_effect=fake_download):
                    output = xiaohongshu_cli.run(VIDEO_URL, Path(directory))
            self.assertEqual(output.name, "video1.md")
            self.assertIn("note_type: video", output.read_text(encoding="utf-8"))
            self.assertTrue((output.parent / "video.mp4").exists())

    def test_run_image_writes_all_image_paths(self):
        def fake_discover(url, profile_dir):
            return ({"platform": "xiaohongshu", "note_id": "image1", "title": "图文", "note_type": "image", "asset_count": 2}, None, ["https://cdn/01.webp", "https://cdn/02.webp"])

        def fake_download(note, video_url, image_urls, output_dir):
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
