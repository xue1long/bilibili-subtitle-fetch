import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

from backends.asr_rescue import run


class BackendResultTest(unittest.TestCase):
    def test_asr_backend_normalizes_success(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "BV1.md"
            with patch("subtitle_rescue.rescue_subtitle", return_value={"path": output, "model": "small"}):
                result = run("BV1", Path(directory), output)
            self.assertEqual(result.status, "success")
            self.assertEqual(result.path, output)
            self.assertEqual(result.model, "small")

    def test_asr_backend_normalizes_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "BV1.md"
            with patch("subtitle_rescue.rescue_subtitle", side_effect=RuntimeError("ffmpeg failed")):
                result = run("BV1", Path(directory), output)
            self.assertEqual(result.status, "failed")
            self.assertIn("ffmpeg failed", result.error)


if __name__ == "__main__":
    unittest.main()
