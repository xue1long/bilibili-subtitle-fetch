import sys
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

from collect_xhs_dates import parse_xhs_date


class ParseXhsDateTest(unittest.TestCase):
    def setUp(self):
        self.today = datetime(2026, 10, 24, 12, 0)

    def test_relative_hours(self):
        self.assertEqual(parse_xhs_date("5小时前", self.today), "2026-10-24")

    def test_relative_days(self):
        self.assertEqual(parse_xhs_date("4天前", self.today), "2026-10-20")

    def test_relative_weeks(self):
        self.assertEqual(parse_xhs_date("2周前", self.today), "2026-10-10")

    def test_relative_months(self):
        self.assertEqual(parse_xhs_date("1个月前", self.today), "2026-09-24")

    def test_relative_years(self):
        self.assertEqual(parse_xhs_date("1年前", self.today), "2025-10-24")

    def test_relative_minutes_and_seconds(self):
        self.assertEqual(parse_xhs_date("30分钟前", self.today), "2026-10-24")
        self.assertEqual(parse_xhs_date("10秒前", self.today), "2026-10-24")

    def test_month_day_keeps_current_year(self):
        self.assertEqual(parse_xhs_date("09-30", self.today), "2026-09-30")

    def test_month_day_in_future_rolls_back_a_year(self):
        # 12-31 is more than a month past 2026-10-24.
        self.assertEqual(parse_xhs_date("12-31", self.today), "2025-12-31")

    def test_month_day_just_ahead_stays(self):
        self.assertEqual(parse_xhs_date("11-05", self.today), "2026-11-05")

    def test_full_date_is_used_as_is(self):
        self.assertEqual(parse_xhs_date("2024-10-09", self.today),
                         "2024-10-09")

    def test_province_suffix_is_ignored(self):
        # The element carries the province too, e.g. "10-09 福建".
        self.assertEqual(parse_xhs_date("10-09 福建", self.today),
                         "2026-10-09")

    def test_edit_prefix_is_stripped(self):
        self.assertEqual(parse_xhs_date("编辑于 09-11", self.today),
                         "2026-09-11")
        self.assertEqual(parse_xhs_date("发布于 09-11", self.today),
                         "2026-09-11")

    def test_edit_prefix_with_relative_time(self):
        self.assertEqual(parse_xhs_date("编辑于 1天前", self.today),
                         "2026-10-23")

    def test_edit_prefix_with_province(self):
        self.assertEqual(parse_xhs_date("编辑于 10-15 浙江", self.today),
                         "2026-10-15")

    def test_rejects_garbage(self):
        for value in ("", "   ", "山东", "13-01", "10-32", "abc"):
            self.assertEqual(parse_xhs_date(value, self.today), "")


if __name__ == "__main__":
    unittest.main()