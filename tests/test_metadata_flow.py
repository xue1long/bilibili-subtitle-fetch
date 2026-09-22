import sys
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

import extract_meta
import prepend_meta
from subtitle_extractor import SubtitleExtractor


HTML = r'''<html><script>
window.__INITIAL_STATE__ = {"videoData":{"title":"测试标题","owner":{"name":"测试UP"},"stat":{"view":123,"like":4},"desc":"第一行\n第二行","pubdate":1735689600}};(function
</script></html>'''
STATE = {
    "videoData": {
        "title": "测试标题",
        "owner": {"name": "测试UP"},
        "stat": {"view": 123, "like": 4},
        "desc": "第一行\n第二行",
        "pubdate": 1735689600,
    }
}


class MetadataFlowTest(unittest.TestCase):
    def test_extract_meta_from_browser_html_returns_fields(self):
        result = extract_meta.extract_meta_from_html("BVTEST123", HTML)

        self.assertEqual(result["title"], "测试标题")
        self.assertEqual(result["uploader"], "测试UP")
        self.assertEqual(result["view_count"], 123)
        self.assertEqual(result["like_count"], 4)
        self.assertEqual(result["description"], "第一行\n第二行")
        self.assertEqual(result["bv"], "BVTEST123")

    def test_extract_meta_from_browser_state_returns_fields(self):
        result = extract_meta.extract_meta_from_state("BVTEST123", STATE)

        self.assertEqual(result["title"], "测试标题")
        self.assertEqual(result["uploader"], "测试UP")
        self.assertEqual(result["view_count"], 123)
        self.assertEqual(result["like_count"], 4)
        self.assertEqual(result["description"], "第一行\n第二行")

    def test_enrichment_uses_browser_html_without_second_http_request(self):
        class Driver:
            def execute_script(self, script):
                self.script = script
                return STATE if "__INITIAL_STATE__" in script else HTML

        with tempfile.TemporaryDirectory() as directory:
            subtitle_path = Path(directory) / "BVTEST123.md"
            subtitle_path.write_text("1\n00:00:00,000 --> 00:00:01,000\n字幕\n", encoding="utf-8")
            extractor = SubtitleExtractor(output_dir=Path(directory), enrich_with_meta=True)
            driver = Driver()

            extractor._enrich_with_meta(subtitle_path, "BVTEST123", driver)

            content = subtitle_path.read_text(encoding="utf-8")
            self.assertIn("__INITIAL_STATE__", driver.script)
            self.assertIn("title: 测试标题", content)
            self.assertIn("uploader: 测试UP", content)
            self.assertIn("description: |", content)

    def test_cli_exits_nonzero_when_metadata_fetch_fails(self):
        with patch.object(extract_meta, "fetch_html", side_effect=RuntimeError("HTTP 412")):
            with patch.object(sys, "argv", ["extract_meta.py", "BVTEST123", "--indent", "0"]):
                with self.assertRaises(SystemExit) as raised:
                    extract_meta.main()

        self.assertEqual(raised.exception.code, 1)

    def test_importing_metadata_module_keeps_parent_stdio(self):
        result = subprocess.run(
            [sys.executable, "-c", "import sys; old=sys.stdout; import extract_meta; print(old is sys.stdout)"],
            cwd=Path(__file__).parents[1] / "scripts",
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "True")

    def test_prepend_meta_supports_douyin_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            subtitle_path = Path(directory) / "DY123.md"
            subtitle_path.write_text(
                "1\n00:00:00,000 --> 00:00:01,000\n字幕\n",
                encoding="utf-8",
            )

            self.assertTrue(
                prepend_meta.prepend_meta(
                    {
                        "platform": "douyin",
                        "video_id": "123",
                        "title": "标题",
                        "uploader": "作者",
                        "description": "简介",
                        "url": "https://www.douyin.com/video/123",
                        "transcript_model": "small",
                    },
                    subtitle_path,
                )
            )

            content = subtitle_path.read_text(encoding="utf-8")
            self.assertIn("platform: douyin", content)
            self.assertIn("video_id: 123", content)
            self.assertIn("url: https://www.douyin.com/video/123", content)
            self.assertIn("description: |", content)
            self.assertIn("transcript_model: small", content)
            self.assertIn("00:00:00,000 --> 00:00:01,000", content)


if __name__ == "__main__":
    unittest.main()
