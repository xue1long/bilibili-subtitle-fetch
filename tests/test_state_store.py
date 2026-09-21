import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

from storage.state_store import load, update


class StateStoreTest(unittest.TestCase):
    def test_loads_legacy_records_and_updates_jobs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            legacy = root / "download_list.json"
            legacy.write_text('{"records":{"BV1":{"status":"success"}}}', encoding="utf-8")
            path = root / "subtitle_jobs.json"
            self.assertEqual(load(path, legacy)["jobs"]["BV1"]["status"], "success")
            update(path, "BV2", "native_running")
            self.assertEqual(load(path)["jobs"]["BV2"]["status"], "native_running")


if __name__ == "__main__":
    unittest.main()
