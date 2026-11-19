import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

import sync_metadata  # noqa: E402


class BackupDbTest(unittest.TestCase):
    def test_backup_copies_db_into_tmp_with_timestamp(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "data" / "subtitle_tasks.db"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"sqlite-payload")

            target = sync_metadata.backup_db(
                root, now=datetime(2026, 11, 19, 0, 55, 12))

            self.assertEqual(target.name, "subtitle_tasks.db.bak.005512")
            self.assertEqual(target.read_bytes(), b"sqlite-payload")
            self.assertEqual(target.parent, root / "tmp")

    def test_backup_returns_none_when_db_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertIsNone(sync_metadata.backup_db(Path(directory)))

    def test_apply_to_store_creates_backup_by_default(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "data" / "subtitle_tasks.db"
            source.parent.mkdir(parents=True)
            con = sqlite3.connect(source)
            con.execute("CREATE TABLE videos (platform TEXT, video_id TEXT, "
                        "title TEXT, uploader TEXT, source_url TEXT)")
            con.commit()
            con.close()
            original = source.read_bytes()

            sync_metadata.apply_to_store(root, {})

            backups = list((root / "tmp").glob("subtitle_tasks.db.bak.*"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_bytes(), original)

    def test_apply_to_store_can_skip_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "data" / "subtitle_tasks.db"
            source.parent.mkdir(parents=True)
            con = sqlite3.connect(source)
            con.execute("CREATE TABLE videos (platform TEXT, video_id TEXT, "
                        "title TEXT, uploader TEXT, source_url TEXT)")
            con.commit()
            con.close()

            sync_metadata.apply_to_store(root, {}, backup=False)

            self.assertFalse((root / "tmp").exists())


if __name__ == "__main__":
    unittest.main()
