import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

from platform_meta_browser import load_done, resolve_xhs_date


class ResolveXhsDateTest(unittest.TestCase):
    def test_keeps_same_year(self):
        self.assertEqual(resolve_xhs_date("03-15", "2026-10-24"),
                         "2026-03-15")

    def test_future_date_belongs_to_previous_year(self):
        # 12-31 is more than a month ahead of 2026-01-05.
        self.assertEqual(resolve_xhs_date("12-31", "2026-01-05"),
                         "2025-12-31")

    def test_near_future_stays_in_current_year(self):
        self.assertEqual(resolve_xhs_date("01-20", "2026-01-05"),
                         "2026-01-20")

    def test_rejects_invalid_input(self):
        for value in ("", "13-01", "03-32", "abc", "2026-03-15"):
            self.assertEqual(resolve_xhs_date(value, "2026-10-24"), "")


class LoadDoneTest(unittest.TestCase):
    def test_only_successful_rows_count_as_done(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cache.jsonl"
            path.write_text(
                json.dumps({"video_id": "a", "ok": True}) + "\n"
                + json.dumps({"video_id": "b", "ok": False}) + "\n"
                + "not json\n",
                encoding="utf-8")
            done = load_done(path)
        self.assertEqual(done, {"a"})

    def test_missing_file_is_empty(self):
        self.assertEqual(load_done(Path("nope-xyz.jsonl")), set())


if __name__ == "__main__":
    unittest.main()