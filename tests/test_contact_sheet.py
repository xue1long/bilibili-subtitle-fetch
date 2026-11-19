import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

from contact_sheet import build_contact_sheet, select_items  # noqa: E402


class ContactSheetTest(unittest.TestCase):
    def setUp(self):
        self.items = [
            {"idx": 1, "raw": "a.img"},
            {"idx": 3, "raw": "b.img"},
            {"idx": 5, "raw": "c.img"},
            {"idx": 9, "raw": "d.img"},
        ]

    def test_idx_mode_selects_by_label_and_skips_gaps(self):
        chosen = select_items(self.items, start=1, count=6)
        self.assertEqual([item["idx"] for item in chosen], [1, 3, 5])

    def test_position_mode_selects_by_array_offset(self):
        chosen = select_items(self.items, start=1, count=2, mode="position")
        self.assertEqual([item["idx"] for item in chosen], [3, 5])

    def test_results_are_sorted_by_idx(self):
        shuffled = [self.items[2], self.items[0], self.items[1]]
        chosen = select_items(shuffled, start=0, count=10)
        self.assertEqual([item["idx"] for item in chosen], [1, 3, 5])

    def test_invalid_arguments_are_rejected(self):
        with self.assertRaises(ValueError):
            select_items(self.items, 0, 0)
        with self.assertRaises(ValueError):
            select_items(self.items, 0, 1, mode="wrong")

    def test_build_sheet_uses_actual_idx_range_and_skips_missing_files(self):
        from PIL import Image

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "a.img").write_bytes(_tiny_image(root / "a.img"))
            (root / "c.img").write_bytes(_tiny_image(root / "c.img"))
            items = [
                {"idx": 1, "raw": str(root / "a.img")},
                {"idx": 3, "raw": str(root / "missing.img")},
                {"idx": 5, "raw": str(root / "c.img")},
            ]
            output, placed = build_contact_sheet(
                items, root / "sheets", start=1, count=6,
                cols=3, cell=40, pad=2, label_height=8)
            self.assertEqual(placed, 2)
            self.assertEqual(output.name, "sheet_0001_0005.png")
            with Image.open(output) as sheet:
                self.assertEqual(sheet.size, (128, 52))

    def test_empty_range_raises(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                build_contact_sheet(self.items, Path(directory),
                                    start=100, count=5)


def _tiny_image(path):
    from io import BytesIO

    from PIL import Image

    buffer = BytesIO()
    Image.new("RGB", (20, 10), (120, 60, 30)).save(buffer, format="PNG")
    return buffer.getvalue()


if __name__ == "__main__":
    unittest.main()
