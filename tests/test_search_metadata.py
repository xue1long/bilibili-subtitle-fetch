import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import fetch_search_bvids
import run_subtitle_batch


class FakeLocator:
    def __init__(self, attrs=None, text=""):
        self.attrs = attrs or {}
        self.text = text

    def get_attribute(self, name):
        return self.attrs.get(name)

    def inner_text(self):
        return self.text


class FakeDetailPage:
    def __init__(self, title=""):
        self._title = title
        self.closed = False
        self.url = None

    def goto(self, url, **kwargs):
        self.url = url

    def title(self):
        return self._title

    def evaluate(self, expression):
        return None

    def close(self):
        self.closed = True


class FakeContext:
    def __init__(self, detail):
        self.detail = detail
        self.created = []

    def new_page(self):
        self.created.append(self.detail)
        return self.detail


class TrackingPage:
    def __init__(self, anchors):
        self.anchors = anchors
        self.goto_calls = []

    def goto(self, *args, **kwargs):
        self.goto_calls.append((args, kwargs))

    def wait_for_selector(self, *args, **kwargs):
        return None

    def query_selector_all(self, *args):
        return self.anchors


class SearchMetadataTest(unittest.TestCase):
    def test_metric_title_is_rejected_and_cleaned(self):
        raw = "7299\n2\n41:06"
        self.assertTrue(fetch_search_bvids.is_metric_title(raw))
        self.assertEqual(fetch_search_bvids.clean_search_title(raw), "")

    def test_real_title_is_preserved(self):
        title = "量化交易入门：从数据到策略"
        self.assertEqual(fetch_search_bvids.clean_search_title(title), title)
        self.assertFalse(fetch_search_bvids.is_metric_title(title))

    def test_bare_numeric_title_is_not_a_metric(self):
        self.assertFalse(fetch_search_bvids.is_metric_title("2024"))
        self.assertEqual(fetch_search_bvids.clean_search_title("100"), "100")

    def test_count_ranking_and_duration_labels_are_metrics(self):
        for title in ("播放 7299", "排名 2", "第 2 名", "41:06"):
            self.assertTrue(fetch_search_bvids.is_metric_title(title), title)
            self.assertEqual(fetch_search_bvids.clean_search_title(title), "")

    def test_card_attributes_use_priority_and_clean_text(self):
        page = TrackingPage([
            FakeLocator(
                {
                    "href": "/video/BV1TEST",
                    "title": "  <em>标题</em>\n  来了  ",
                    "aria-label": "次要标题",
                    "data-title": "再次要标题",
                }
            )
        ])
        with patch.object(fetch_search_bvids.time, "sleep"):
            videos = fetch_search_bvids.fetch_page(
                page, "x", 1, limiter=SimpleNamespace(wait=lambda: None)
            )
        self.assertEqual(videos[0]["title"], "标题 来了")
        self.assertEqual(videos[0]["title_source"], "card")

    def test_rejected_title_falls_through_to_next_attribute(self):
        page = TrackingPage([
            FakeLocator(
                {
                    "href": "/video/BV1TEST",
                    "title": "7299\n2\n41:06",
                    "aria-label": "真实 aria 标题",
                    "data-title": "不应使用",
                }
            )
        ])
        with patch.object(fetch_search_bvids.time, "sleep"):
            videos = fetch_search_bvids.fetch_page(
                page, "x", 1, limiter=SimpleNamespace(wait=lambda: None)
            )
        self.assertEqual(videos[0]["title"], "真实 aria 标题")
        self.assertEqual(videos[0]["title_source"], "card")

    def test_missing_search_limiter_does_not_navigate(self):
        page = TrackingPage([])
        with self.assertRaises(ValueError):
            fetch_search_bvids.fetch_page(page, "x", 1, limiter=None)
        self.assertEqual(page.goto_calls, [])

    def test_detail_fallback_waits_closes_temp_page_and_preserves_search_page(self):
        detail = FakeDetailPage("真实视频标题")
        search_page = SimpleNamespace(context=FakeContext(detail), url="search-page")
        limiter = SimpleNamespace(wait=unittest.mock.Mock())

        title = fetch_search_bvids.resolve_detail_title(search_page, "BV1TEST", limiter)

        self.assertEqual(title, "真实视频标题")
        limiter.wait.assert_called_once_with()
        self.assertTrue(detail.closed)
        self.assertEqual(search_page.url, "search-page")

    def test_generic_detail_title_and_missing_limiter_are_unresolved(self):
        detail = FakeDetailPage("哔哩哔哩 (゜-゜)つロ 干杯~-bilibili")
        search_page = SimpleNamespace(context=FakeContext(detail))
        self.assertEqual(
            fetch_search_bvids.resolve_detail_title(search_page, "BV1TEST", None),
            "",
        )
        self.assertEqual(search_page.context.created, [])

        limiter = SimpleNamespace(wait=lambda: None)
        self.assertEqual(
            fetch_search_bvids.resolve_detail_title(search_page, "BV1TEST", limiter),
            "",
        )
        self.assertTrue(detail.closed)

    def test_exact_bilibili_detail_title_is_unresolved(self):
        detail = FakeDetailPage("bilibili")
        search_page = SimpleNamespace(context=FakeContext(detail))
        limiter = SimpleNamespace(wait=lambda: None)
        self.assertEqual(
            fetch_search_bvids.resolve_detail_title(search_page, "BV1TEST", limiter),
            "",
        )

    def test_detail_title_platform_suffix_is_preserved_when_real(self):
        detail = FakeDetailPage("真实标题_哔哩哔哩_bilibili")
        search_page = SimpleNamespace(context=FakeContext(detail))
        limiter = SimpleNamespace(wait=lambda: None)
        self.assertEqual(
            fetch_search_bvids.resolve_detail_title(search_page, "BV1TEST", limiter),
            "真实标题_哔哩哔哩_bilibili",
        )

    def test_batch_keeps_processing_unresolved_title(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "videos.json"
            source.write_text(
                json.dumps(
                    {
                        "keyword": "x",
                        "videos": [
                            {"bvid": "BV1", "title": "", "title_source": "unresolved"}
                        ],
                    }
                ),
                encoding="utf-8",
            )
            extractor = SimpleNamespace(
                last_probe=None,
                last_duration_sec=0.0,
                breaker=SimpleNamespace(state="CLOSED"),
            )
            with patch.object(run_subtitle_batch, "patch_selenium_edge", return_value=False), patch.object(
                run_subtitle_batch, "load_skill_module", return_value=SimpleNamespace(
                    SubtitleExtractor=lambda **kwargs: extractor
                ),
            ), patch.object(run_subtitle_batch, "run_one", return_value=("success", "ok", None)), patch.object(
                sys, "argv", ["run_subtitle_batch.py", str(source), tmp, "edge", "--min-delay", "0", "--max-delay", "0"]
            ):
                with patch("bili_guard.normalize_subtitle_probe", return_value={}):
                    self.assertEqual(run_subtitle_batch.main(), 0)
            results = json.loads((Path(tmp) / "x_results.json").read_text(encoding="utf-8"))
            self.assertEqual(results["success"][0]["title"], "")


if __name__ == "__main__":
    unittest.main()
