import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

from summary_pipeline import (  # noqa: E402
    SummaryStore,
    classify_source,
    download_cover,
    index_items,
    is_placeholder,
    merge_batch,
    to_png,
    verify_summaries,
    write_summaries,
)


class IndexSafetyTest(unittest.TestCase):
    """The idx-205 bug: array position and idx value diverge after a gap."""

    def setUp(self):
        # Cover 205 failed to download, so it is absent from the manifest.
        self.items = [
            {"idx": 204, "record_id": "rec204", "title": "前一条"},
            {"idx": 206, "record_id": "rec206", "title": "后一条"},
        ]

    def test_index_items_uses_explicit_idx_not_array_position(self):
        indexed = index_items(self.items)
        self.assertEqual(indexed[206]["record_id"], "rec206")
        self.assertNotIn(205, indexed)

    def test_merge_batch_does_not_shift_metadata_after_a_gap(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SummaryStore(Path(tmp) / "summaries.json")
            report = merge_batch(store, self.items,
                                 [{"idx": 206, "summary": "后一条摘要"}])
            self.assertEqual(report["added"], 1)
            self.assertEqual(report["missing_item"], [])
            self.assertEqual(store.data["206"]["record_id"], "rec206")
            self.assertEqual(store.data["206"]["title"], "后一条")

    def test_merge_batch_rejects_unknown_idx_instead_of_wrong_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SummaryStore(Path(tmp) / "summaries.json")
            report = merge_batch(store, self.items,
                                 [{"idx": 205, "summary": "不该写入"}])
            self.assertEqual(report["missing_item"], [205])
            self.assertEqual(store.data, {})

    def test_store_round_trip_preserves_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nested" / "summaries.json"
            store = SummaryStore(path)
            store.put(1, "rec1", "标题", " 摘要 ")
            self.assertEqual(store.save(), 1)
            reloaded = SummaryStore(path)
            self.assertEqual(reloaded.rows(),
                             [{"record_id": "rec1", "summary": "摘要"}])
            self.assertEqual(reloaded.expected(), {"rec1": "摘要"})


class SourceClassificationTest(unittest.TestCase):
    def test_placeholder_titles_are_skipped(self):
        for title in ("", "已失效视频", "BV1xx411c7mD"):
            self.assertTrue(is_placeholder(title))
            self.assertEqual(classify_source(title, "cover.jpg"), "skip")

    def test_cover_and_title_both_available(self):
        self.assertEqual(classify_source("标题", "https://cover.jpg"),
                         "cover+title")

    def test_title_only_fallback(self):
        self.assertEqual(classify_source("标题"), "title")


class CoverDownloadTest(unittest.TestCase):
    def test_download_normalizes_protocol_and_uses_injected_fetch(self):
        seen = []

        def fetch(url):
            seen.append(url)
            return b"image-bytes"

        with tempfile.TemporaryDirectory() as tmp:
            dest = download_cover("//i0.hdslb.com/a.jpg", Path(tmp) / "a.img",
                                  fetch=fetch)
            self.assertEqual(seen, ["https://i0.hdslb.com/a.jpg"])
            self.assertEqual(dest.read_bytes(), b"image-bytes")

    def test_download_returns_none_on_empty_or_failing_fetch(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(download_cover(
                "https://x/a.jpg", Path(tmp) / "a.img", fetch=lambda u: b""))
            def boom(url):
                raise OSError("offline")
            self.assertIsNone(download_cover(
                "https://x/b.jpg", Path(tmp) / "b.img", fetch=boom))

    def test_to_png_converts_webp_like_input(self):
        from PIL import Image

        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "a.webp"
            dest = Path(tmp) / "a.png"
            Image.new("RGB", (4, 3), (10, 20, 30)).save(src, format="WEBP")
            result = to_png(src, dest)
            with Image.open(result) as image:
                self.assertEqual(image.format, "PNG")
                self.assertEqual(image.size, (4, 3))


class FakeBase:
    def __init__(self):
        self.updated = None
        self.verify_args = None

    def update_records(self, table_id, records):
        self.updated = (table_id, records)
        return len(records)

    def verify_field(self, table_id, field, expected, **kwargs):
        self.verify_args = (table_id, field, expected, kwargs)
        return {"checked": len(expected), "mismatch": 0, "missing": 0}


class FeishuBridgeTest(unittest.TestCase):
    def test_write_summaries_shapes_records_as_record_id_map(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SummaryStore(Path(tmp) / "s.json")
            store.put(1, "rec1", "标题", "摘要一")
            base = FakeBase()
            self.assertEqual(write_summaries(base, "tblX", store), 1)
            table, records = base.updated
            self.assertEqual(table, "tblX")
            self.assertEqual(records, {"rec1": {"摘要": "摘要一"}})

    def test_verify_summaries_passes_platform_filter(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SummaryStore(Path(tmp) / "s.json")
            store.put(1, "rec1", "标题", "摘要一")
            base = FakeBase()
            verify_summaries(base, "tblX", store, filter_value="bilibili",
                             concurrency=4)
            table, field, expected, kwargs = base.verify_args
            self.assertEqual((table, field), ("tblX", "摘要"))
            self.assertEqual(expected, {"rec1": "摘要一"})
            self.assertEqual(kwargs["filter_value"], "bilibili")
            self.assertEqual(kwargs["concurrency"], 4)


if __name__ == "__main__":
    unittest.main()
