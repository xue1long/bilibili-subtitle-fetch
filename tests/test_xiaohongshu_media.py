import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

from xiaohongshu_media import (
    classify_assets,
    normalize_metadata,
    parse_note_id,
    select_note_images,
    scaled_dimensions,
    discover_note,
    XiaohongshuError,
    _normalize_media_url,
    _note_state_script,
    _page_state_script,
)


class XiaohongshuMediaTest(unittest.TestCase):
    def test_parse_note_id_from_explore_and_board_urls(self):
        self.assertEqual(
            parse_note_id("https://www.xiaohongshu.com/explore/6aa540d0000000002902d565?xsec_token=secret"),
            "6aa540d0000000002902d565",
        )
        self.assertEqual(parse_note_id("https://www.xiaohongshu.com/board/6aa540d0000000002902d565"), "6aa540d0000000002902d565")

    def test_normalize_metadata_removes_query_token(self):
        result = normalize_metadata(
            {
                "title": "图文标题",
                "description": "笔记简介",
                "uploader": "作者",
                "published_label": "09-12 福建",
                "note_type": "image",
                "asset_count": 4,
            },
            "https://www.xiaohongshu.com/explore/abc?xsec_token=secret&xsec_source=pc_user",
        )
        self.assertEqual(result["note_id"], "abc")
        self.assertEqual(result["url"], "https://www.xiaohongshu.com/explore/abc")
        self.assertNotIn("secret", result["url"])
        self.assertEqual(result["platform"], "xiaohongshu")

    def test_video_takes_priority_over_images(self):
        kind, assets = classify_assets(["https://cdn/video.mp4"], ["https://cdn/01.webp"])
        self.assertEqual(kind, "video")
        self.assertEqual(assets, ["https://cdn/video.mp4"])

    def test_image_assets_are_deduplicated(self):
        kind, assets = classify_assets([], ["https://cdn/01.webp", "https://cdn/01.webp", "https://cdn/02.webp"])
        self.assertEqual(kind, "image")
        self.assertEqual(assets, ["https://cdn/01.webp", "https://cdn/02.webp"])

    def test_image_scale_keeps_ratio_at_half(self):
        self.assertEqual(scaled_dimensions(1080, 1800), (540, 900))
        self.assertEqual(scaled_dimensions(1079, 1801), (539, 900))

    def test_select_note_images_excludes_small_ui_images(self):
        images = [
            {"url": "https://sns-avatar-qc.xhscdn.com/avatar.webp", "width": 120, "height": 120},
            {"url": "https://sns-webpic-qc.xhscdn.com/01.webp", "width": 1080, "height": 1800},
            {"url": "https://sns-webpic-qc.xhscdn.com/01.webp", "width": 1080, "height": 1800},
            {"url": "https://sns-webpic-qc.xhscdn.com/02.webp", "width": 1080, "height": 1800},
        ]
        self.assertEqual(
            select_note_images(images),
            ["https://sns-webpic-qc.xhscdn.com/01.webp", "https://sns-webpic-qc.xhscdn.com/02.webp"],
        )

    def test_login_redirect_has_explicit_error(self):
        with patch("xiaohongshu_media._discover_page", return_value={"login_required": True}):
            with self.assertRaises(XiaohongshuError) as context:
                discover_note("https://www.xiaohongshu.com/explore/abc")
            self.assertEqual(context.exception.code, "LOGIN_REQUIRED")

    def test_page_script_reads_current_description_container(self):
        script = _page_state_script()
        self.assertIn(".detail-desc, .note-content", script)
        self.assertIn("#noteContainer, .note-container", script)

    def test_note_state_script_prefers_page_state_image_list(self):
        script = _note_state_script()
        self.assertIn("__INITIAL_STATE__", script)
        self.assertIn("imageList", script)
        self.assertIn("authoritative", script)

    def test_media_url_is_upgraded_to_https(self):
        self.assertEqual(
            _normalize_media_url("//sns-webpic-qc.xhscdn.com/a.webp"),
            "https://sns-webpic-qc.xhscdn.com/a.webp",
        )
        self.assertEqual(
            _normalize_media_url("http://sns-webpic-qc.xhscdn.com/a.webp"),
            "https://sns-webpic-qc.xhscdn.com/a.webp",
        )
        self.assertEqual(
            _normalize_media_url("https://sns-webpic-qc.xhscdn.com/a.webp"),
            "https://sns-webpic-qc.xhscdn.com/a.webp",
        )

    def test_discover_note_prefers_cdp_path(self):
        state = {
            "title": "标题",
            "description": "简介",
            "author": "作者",
            "published": "09-12 福建",
            "videos": [],
            "images": [{"url": "https://sns-webpic-qc.xhscdn.com/01.webp", "width": 1080, "height": 1800}],
        }
        with patch("xiaohongshu_media._discover_via_cdp", return_value=state) as cdp:
            with patch("xiaohongshu_media._discover_page", side_effect=AssertionError("不该走匿名路径")):
                metadata, video_url, image_urls = discover_note(
                    "https://www.xiaohongshu.com/explore/abc?xsec_token=secret",
                    Path("/tmp/profile"),
                )
        cdp.assert_called_once()
        self.assertIsNone(video_url)
        self.assertEqual(image_urls, ["https://sns-webpic-qc.xhscdn.com/01.webp"])
        self.assertEqual(metadata["note_type"], "image")
        self.assertEqual(metadata["published_label"], "09-12 福建")


if __name__ == "__main__":
    unittest.main()
