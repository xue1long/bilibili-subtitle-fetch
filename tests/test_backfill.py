import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

from backfill_platform_records import backfill
from storage.platform_manifest import load as load_manifest
from storage.sqlite_store import SQLiteStore


def _make_douyin_note(directory: Path, video_id: str, with_md: bool = True) -> Path:
    target = directory / "10_raw" / "02_抖音视频笔记"
    target.mkdir(parents=True, exist_ok=True)
    if with_md:
        (target / f"{video_id}.md").write_text(f"# {video_id}\n", encoding="utf-8")
    (target / f"{video_id}.mp4").write_bytes(b"video")
    return target


def _make_xhs_flat_note(directory: Path, note_id: str, with_md: bool = True) -> Path:
    target = directory / "10_raw" / "03_小红书收藏夹"
    target.mkdir(parents=True, exist_ok=True)
    if with_md:
        (target / f"{note_id}.md").write_text(f"# {note_id}\n", encoding="utf-8")
    (target / f"{note_id}.mp4").write_bytes(b"video")
    return target


def _make_xhs_nested_note(directory: Path, note_id: str, asset_count: int = 1) -> Path:
    target = directory / "10_raw" / "03_小红书"
    note_dir = target / note_id
    note_dir.mkdir(parents=True, exist_ok=True)
    (note_dir / f"{note_id}.md").write_text(f"# {note_id}\n", encoding="utf-8")
    for index in range(1, asset_count + 1):
        (note_dir / "images" / f"{index:02d}.webp").parent.mkdir(parents=True, exist_ok=True)
        (note_dir / "images" / f"{index:02d}.webp").write_bytes(b"image")
    return target


class BackfillTest(unittest.TestCase):
    def test_dry_run_returns_scan_counts_without_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _make_douyin_note(root, "1111111111111111111")
            _make_xhs_flat_note(root, "6aabba200000000025034beb")
            summary = backfill(root, write=False)
            self.assertEqual(summary["douyin"]["scanned"], 1)
            self.assertEqual(summary["xiaohongshu"]["scanned"], 1)
            # No manifest/db files should have been created
            self.assertFalse((root / "data" / "platform_videos_manifest.json").exists())

    def test_write_populates_manifest_and_sqlite(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _make_douyin_note(root, "1111111111111111111")
            _make_xhs_flat_note(root, "6aabba200000000025034beb")
            _make_xhs_nested_note(root, "6aa540d0000000002902d565", asset_count=2)

            summary = backfill(root, write=True)
            self.assertEqual(summary["douyin"]["written"], 1)
            self.assertEqual(summary["xiaohongshu"]["written"], 2)

            manifest = load_manifest(root / "data" / "platform_videos_manifest.json")
            self.assertIn("1111111111111111111", manifest["sources"]["douyin"])
            self.assertIn("6aabba200000000025034beb", manifest["sources"]["xiaohongshu"])
            self.assertIn("6aa540d0000000002902d565", manifest["sources"]["xiaohongshu"])

            store = SQLiteStore(root / "data" / "subtitle_tasks.db")
            self.assertEqual(store.jobs("douyin")["1111111111111111111"]["status"], "backfilled")
            self.assertEqual(store.jobs("xiaohongshu")["6aabba200000000025034beb"]["status"], "backfilled")
            xhs = store.jobs("xiaohongshu")["6aa540d0000000002902d565"]
            self.assertEqual(xhs["status"], "backfilled")

    def test_backfill_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _make_douyin_note(root, "2222222222222222222")
            first = backfill(root, write=True)
            second = backfill(root, write=True)
            self.assertEqual(first["douyin"]["written"], 1)
            self.assertEqual(second["douyin"]["written"], 1)
            store = SQLiteStore(root / "data" / "subtitle_tasks.db")
            self.assertEqual(len(store.jobs("douyin")), 1)

    def test_records_orphan_mp4_with_no_md(self):
        """An orphan mp4 without a matching .md is still recorded so the id is
        not re-downloaded."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bad = root / "10_raw" / "02_抖音视频笔记"
            bad.mkdir(parents=True, exist_ok=True)
            (bad / "9999999999999999999.mp4").write_bytes(b"video")
            summary = backfill(root, write=True)
            self.assertEqual(summary["douyin"]["written"], 1)
            store = SQLiteStore(root / "data" / "subtitle_tasks.db")
            self.assertEqual(store.jobs("douyin")["9999999999999999999"]["status"], "backfilled")

    def test_skips_files_with_invalid_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bad = root / "10_raw" / "02_抖音视频笔记"
            bad.mkdir(parents=True, exist_ok=True)
            (bad / "not-a-valid-id.mp4").write_bytes(b"video")
            (bad / "not-a-valid-id.md").write_text("# x", encoding="utf-8")
            summary = backfill(root, write=True)
            self.assertEqual(summary["douyin"]["written"], 0)


if __name__ == "__main__":
    unittest.main()