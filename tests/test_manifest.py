import json
import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

from sources.models import Video
from storage.manifest import load, upsert


class ManifestTest(unittest.TestCase):
    def test_upsert_preserves_first_seen_and_updates_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "videos_manifest.json"
            upsert(path, [Video("BV1234567890", title="old", source_type="single")])
            first = load(path)["videos"]["BV1234567890"]["first_seen_at"]
            upsert(path, [Video("BV1234567890", title="new", source_type="favorite")])
            record = load(path)["videos"]["BV1234567890"]
            self.assertEqual(record["first_seen_at"], first)
            self.assertEqual(record["title"], "new")
            self.assertEqual(record["source_type"], "favorite")


if __name__ == "__main__":
    unittest.main()
