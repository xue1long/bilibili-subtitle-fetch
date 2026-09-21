import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

from sources.favorite import _page_url


class FavoriteSourceTest(unittest.TestCase):
    def test_page_url_preserves_favorite_id_and_sets_page(self):
        url = _page_url("https://space.bilibili.com/30210365/favlist?fid=3745603465&ftype=create", 12)
        self.assertIn("fid=3745603465", url)
        self.assertIn("ftype=create", url)
        self.assertIn("pn=12", url)


if __name__ == "__main__":
    unittest.main()
