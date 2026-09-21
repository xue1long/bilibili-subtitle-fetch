import unittest
from pathlib import Path


class DeliveryManifestTest(unittest.TestCase):
    def test_deliverable_scripts_exist(self):
        expected = {
            "scripts/subtitle_extractor.py",
            "scripts/extract_meta.py",
            "scripts/prepend_meta.py",
            "scripts/fetch_search_bvids.py",
            "scripts/run_subtitle_batch.py",
            "scripts/_driver_patch.py",
            "scripts/rescue_one_subtitle.py",
        }
        for path in expected:
            self.assertTrue(Path(path).exists(), path)


if __name__ == "__main__":
    unittest.main()
