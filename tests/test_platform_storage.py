import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

from sources.models import Video as BVVideo
from storage.platform_manifest import (
    SUPPORTED_PLATFORMS,
    load as load_manifest,
    manifest_path,
    mark_completed,
    upsert,
)
from storage.sqlite_store import PLATFORM_BILIBILI, SQLiteStore


class PlatformManifestTest(unittest.TestCase):
    def test_upsert_is_idempotent_and_updates_last_seen(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "platform_videos_manifest.json"
            upsert(path, "douyin", [{"video_id": "v1", "title": "标题"}])
            upsert(path, "douyin", [{"video_id": "v1", "title": "新标题"}])
            data = load_manifest(path)
            entry = data["sources"]["douyin"]["v1"]
            self.assertEqual(entry["title"], "新标题")
            self.assertEqual(entry["video_id"], "v1")
            self.assertEqual(data["sources"]["douyin"]["v1"], entry)
            self.assertEqual(set(data["sources"].keys()), set(SUPPORTED_PLATFORMS))

    def test_mark_completed_writes_status_and_output(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "platform_videos_manifest.json"
            upsert(path, "xiaohongshu", [{"note_id": "n1", "video_id": "n1"}])
            mark_completed(path, "xiaohongshu", "n1", status="assets_downloaded", output_path="/tmp/xhs/n1")
            entry = load_manifest(path)["sources"]["xiaohongshu"]["n1"]
            self.assertEqual(entry["status"], "assets_downloaded")
            self.assertEqual(entry["output_path"], "/tmp/xhs/n1")
            self.assertTrue(entry["completed_at"])

    def test_manifest_path_uses_data_dir(self):
        root = Path("/tmp/project-root")
        self.assertEqual(manifest_path(root), root / "data" / "platform_videos_manifest.json")


class SQLiteStorePlatformTest(unittest.TestCase):
    def test_douyin_record_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteStore(Path(directory) / "tasks.db")
            store.upsert_videos([BVVideo(bvid="DV1111", title="dy-title", source_type="douyin_cli")], platform="douyin")
            store.update_job(("douyin", "DV1111"), "asr_success", source="asr")
            record = store.jobs("douyin").get("DV1111")
            self.assertIsNotNone(record)
            self.assertEqual(record["status"], "asr_success")
            self.assertEqual(record["source"], "asr")
            self.assertTrue(store.is_completed("douyin", "DV1111"))

    def test_xiaohongshu_record_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteStore(Path(directory) / "tasks.db")
            store.upsert_videos([BVVideo(bvid="XH2222", title="xhs-title", source_type="xiaohongshu_cli")], platform="xiaohongshu")
            store.update_job(("xiaohongshu", "XH2222"), "assets_downloaded")
            record = store.jobs("xiaohongshu").get("XH2222")
            self.assertEqual(record["status"], "assets_downloaded")

    def test_bilibili_path_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteStore(Path(directory) / "tasks.db")
            store.upsert_videos([BVVideo(bvid="BV1234567890", title="bili", source_type="favorite")])
            store.update_job("BV1234567890", "native_success", source="native")
            self.assertEqual(store.jobs("bilibili")["BV1234567890"]["status"], "native_success")
            self.assertTrue(store.is_completed("bilibili", "BV1234567890"))

    def test_platforms_do_not_collide(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteStore(Path(directory) / "tasks.db")
            store.update_job(("bilibili", "123"), "native_success")
            store.update_job(("douyin", "123"), "asr_success")
            self.assertEqual(store.jobs("bilibili")["123"]["status"], "native_success")
            self.assertEqual(store.jobs("douyin")["123"]["status"], "asr_success")
            self.assertFalse(store.is_completed("douyin", "1234"))

    def test_migration_preserves_bilibili_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "tasks.db"
            # Seed legacy schema
            legacy = """
            CREATE TABLE videos (bvid TEXT PRIMARY KEY, title TEXT NOT NULL DEFAULT '',
                uploader TEXT NOT NULL DEFAULT '', source_type TEXT NOT NULL DEFAULT '',
                source_url TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE jobs (bvid TEXT PRIMARY KEY REFERENCES videos(bvid),
                status TEXT NOT NULL, error TEXT NOT NULL DEFAULT '', source TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE subtitle_files (id INTEGER PRIMARY KEY AUTOINCREMENT,
                bvid TEXT NOT NULL REFERENCES videos(bvid), path TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT '', model TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
            """
            import sqlite3
            db = sqlite3.connect(db_path)
            try:
                db.executescript(legacy)
                db.execute("INSERT INTO videos(bvid, title, source_type) VALUES (?, ?, ?)", ("BVABCDEFGHIJ", "legacy", "favorite"))
                db.execute("INSERT INTO jobs(bvid, status, source) VALUES (?, ?, ?)", ("BVABCDEFGHIJ", "native_success", "native"))
                db.commit()
            finally:
                db.close()

            # Constructor should migrate in place
            store = SQLiteStore(db_path)
            record = store.jobs("bilibili")["BVABCDEFGHIJ"]
            self.assertEqual(record["status"], "native_success")
            self.assertEqual(record["source"], "native")


if __name__ == "__main__":
    unittest.main()