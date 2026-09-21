import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

from sources.models import normalize_bvid
from sources.single_video import discover


class SourceAdapterTest(unittest.TestCase):
    def test_normalize_bvid_from_url(self):
        self.assertEqual(normalize_bvid("https://www.bilibili.com/video/BV1vqeF62EQS?p=1"), "BV1vqeF62EQS")

    def test_single_source_returns_uniform_video(self):
        video = discover("BV1vqeF62EQS")[0]
        self.assertEqual(video.bvid, "BV1vqeF62EQS")
        self.assertEqual(video.source_type, "single")

    def test_invalid_single_source_is_rejected(self):
        with self.assertRaises(ValueError):
            discover("not-a-bvid")


if __name__ == "__main__":
    unittest.main()
