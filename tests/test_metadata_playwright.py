import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import sys

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

from subtitle_extractor import SubtitleExtractor


class PlaywrightMetadataTest(unittest.TestCase):
    def test_asr_fallback_enriches_successful_output(self):
        with tempfile.TemporaryDirectory() as directory:
            extractor = SubtitleExtractor(output_dir=Path(directory), min_delay=0, max_delay=0, enrich_with_meta=True)
            result = {"path": Path(directory) / "BV1.md", "model": "small"}
            result["path"].write_text("1\n00:00:00,000 --> 00:00:01,000\ntext\n", encoding="utf-8")
            with patch("backends.asr_rescue.run", return_value=type("R", (), {"status": "success", "path": result["path"], "model": "small"})()), \
                    patch.object(extractor, "_enrich_with_meta") as enrich:
                extractor._started_at = 0
                extractor._run_asr_fallback("BV1")
            enrich.assert_called_once_with(result["path"], "BV1")


if __name__ == "__main__":
    unittest.main()
