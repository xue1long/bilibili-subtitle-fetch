import unittest
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

from rescue_one_subtitle import update_download_list
from bili_guard import FailureKind


class RescueContractTest(unittest.TestCase):
    def test_batch_rescue_is_not_advertised_without_an_implementation(self):
        skill = Path("SKILL.md").read_text(encoding="utf-8")
        readme = Path("README.md").read_text(encoding="utf-8")
        self.assertNotIn("rescue_failed_subtitles.py", skill)
        self.assertNotIn("rescue_failed_subtitles.py", readme)

    def test_rescue_updates_shared_download_list(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            subtitle = output_dir / "BVtest.md"
            update_download_list(output_dir, "BVtest", "success", subtitle)
            data = __import__("json").loads(
                (output_dir / "download_list.json").read_text(encoding="utf-8")
            )
            self.assertEqual(data["records"]["BVtest"]["status"], "success")
            self.assertEqual(data["records"]["BVtest"]["subtitle_path"], str(subtitle))

    def test_rescue_persists_shared_failure_code(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            update_download_list(
                output_dir,
                "BVtest",
                "paused",
                error="HTTP 412",
                error_code=FailureKind.RATE_LIMITED,
                retry_after="2099-01-01T00:00:00Z",
            )
            data = __import__("json").loads(
                (output_dir / "download_list.json").read_text(encoding="utf-8")
            )
            record = data["records"]["BVtest"]
            self.assertEqual(record["error_code"], FailureKind.RATE_LIMITED)
            self.assertEqual(record["retry_after"], "2099-01-01T00:00:00Z")


if __name__ == "__main__":
    unittest.main()
