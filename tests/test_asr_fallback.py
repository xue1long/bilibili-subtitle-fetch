import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import sys
sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

from bili_guard import FailureKind
from subtitle_extractor import SubtitleExtractor


class AsrFallbackTest(unittest.TestCase):
    def make_extractor(self, directory, enabled=True):
        return SubtitleExtractor(
            output_dir=Path(directory), enrich_with_meta=False,
            backend="playwright", asr_fallback=enabled, min_delay=0, max_delay=0,
        )

    def test_no_subtitle_invokes_rescue_when_enabled(self):
        with tempfile.TemporaryDirectory() as directory:
            extractor = self.make_extractor(directory)
            with patch.object(extractor, "_extract_single_playwright", side_effect=RuntimeError("未捕获到 AI 字幕响应")), \
                    patch.object(extractor, "_run_asr_fallback", return_value="rescued.md") as rescue:
                self.assertEqual(extractor._extract_single_once("BV1"), "rescued.md")
            rescue.assert_called_once_with("BV1")

    def test_no_subtitle_does_not_invoke_rescue_by_default(self):
        with tempfile.TemporaryDirectory() as directory:
            extractor = self.make_extractor(directory, enabled=False)
            with patch.object(extractor, "_extract_single_playwright", side_effect=RuntimeError("未捕获到 AI 字幕响应")), \
                    patch.object(extractor, "_run_asr_fallback") as rescue:
                self.assertIsNone(extractor._extract_single_once("BV1"))
            rescue.assert_not_called()
            self.assertEqual(extractor.last_failure_kind, FailureKind.NO_SUBTITLE)

    def test_browser_failure_does_not_invoke_rescue(self):
        with tempfile.TemporaryDirectory() as directory:
            extractor = self.make_extractor(directory)
            with patch.object(extractor, "_extract_single_playwright", side_effect=RuntimeError("playwright executable not found")), \
                    patch.object(extractor, "_run_asr_fallback") as rescue:
                self.assertIsNone(extractor._extract_single_once("BV1"))
            rescue.assert_not_called()


if __name__ == "__main__":
    unittest.main()
