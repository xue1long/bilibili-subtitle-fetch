import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

import douyin_cli


URL = "https://www.douyin.com/user/self?modal_id=123456&showTab=favorite_collection"


class DouyinCliTest(unittest.TestCase):
    def test_run_writes_metadata_before_srt(self):
        def fake_download(url, video_path, profile_dir):
            video_path.write_bytes(b"video")
            return {
                "platform": "douyin",
                "video_id": "123456",
                "title": "标题",
                "uploader": "作者",
                "description": "简介",
                "url": "https://www.douyin.com/video/123456",
            }

        with tempfile.TemporaryDirectory() as directory:
            with patch.object(douyin_cli, "download_video", side_effect=fake_download):
                with patch.object(douyin_cli.subprocess, "run", return_value=SimpleNamespace(returncode=0, stderr=b"")):
                    with patch.object(douyin_cli, "transcribe", return_value="1\n00:00:00,000 --> 00:00:01,000\n字幕\n"):
                        output = douyin_cli.run(URL, Path(directory), model_size="small")

            content = output.read_text(encoding="utf-8")
            self.assertEqual(output.name, "DY123456.md")
            self.assertLess(content.index("platform: douyin"), content.index("00:00:00,000"))
            self.assertIn("description: |", content)
            self.assertIn("transcript_model: small", content)

    def test_failed_asr_does_not_leave_markdown(self):
        def fake_download(url, video_path, profile_dir):
            video_path.write_bytes(b"video")
            return {"video_id": "123456", "platform": "douyin"}

        with tempfile.TemporaryDirectory() as directory:
            with patch.object(douyin_cli, "download_video", side_effect=fake_download):
                with patch.object(douyin_cli.subprocess, "run", return_value=SimpleNamespace(returncode=0, stderr=b"")):
                    with patch.object(douyin_cli, "transcribe", side_effect=RuntimeError("ASR failed")):
                        with self.assertRaises(douyin_cli.DouyinCliError):
                            douyin_cli.run(URL, Path(directory))
            self.assertFalse((Path(directory) / "DY123456.md").exists())


if __name__ == "__main__":
    unittest.main()
