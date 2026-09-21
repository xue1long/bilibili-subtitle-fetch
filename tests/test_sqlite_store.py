import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

from sources.models import Video
from storage.sqlite_store import SQLiteStore


class SQLiteStoreTest(unittest.TestCase):
    def test_video_and_job_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteStore(Path(directory) / "tasks.db")
            store.upsert_videos([Video("BV1234567890", title="title", source_type="favorite")])
            store.update_job("BV1234567890", "native_success", source="native")
            record = store.jobs()["BV1234567890"]
            self.assertEqual(record["status"], "native_success")
            self.assertEqual(record["source"], "native")


if __name__ == "__main__":
    unittest.main()
