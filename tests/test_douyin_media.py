import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

from douyin_media import extract_media_url_from_html, extract_page_metadata, parse_video_id


class DouyinMediaTest(unittest.TestCase):
    def test_parse_video_id_from_modal_id(self):
        self.assertEqual(
            parse_video_id(
                "https://www.douyin.com/user/self?modal_id=7687559858779351972&showTab=favorite_collection"
            ),
            "7687559858779351972",
        )

    def test_parse_video_id_from_video_path(self):
        self.assertEqual(parse_video_id("https://www.douyin.com/video/123456"), "123456")

    def test_extract_page_metadata(self):
        result = extract_page_metadata(
            {
                "aweme_detail": {
                    "aweme_id": "123456",
                    "desc": "视频简介",
                    "create_time": 1735689600,
                    "author": {"nickname": "作者"},
                }
            },
            "https://www.douyin.com/video/123456",
        )
        self.assertEqual(result["platform"], "douyin")
        self.assertEqual(result["video_id"], "123456")
        self.assertEqual(result["title"], "视频简介")
        self.assertEqual(result["uploader"], "作者")
        self.assertEqual(result["description"], "视频简介")
        self.assertEqual(result["url"], "https://www.douyin.com/video/123456")
        self.assertIn("video_published_at", result)

    def test_media_url_must_match_target_id(self):
        html = r'''<script>
        {"aweme_id":"999","playAddr":"https://cdn.invalid/decoy.mp4"}
        {"aweme_id":"123","playAddr":"https://cdn.invalid/target.mp4"}
        </script>'''
        self.assertEqual(
            extract_media_url_from_html(html, "123"),
            "https://cdn.invalid/target.mp4",
        )

    def test_media_url_rejects_unassociated_candidates(self):
        html = r'''<script>{"playAddr":"https://cdn.invalid/only.mp4"}</script>'''
        self.assertIsNone(extract_media_url_from_html(html, "123"))

    def test_media_url_can_follow_modal_id_when_aweme_id_is_absent(self):
        html = r'''<script>{"modal_id":"123","playAddr":"https://cdn.invalid/target.mp4"}</script>'''
        self.assertEqual(extract_media_url_from_html(html, "123"), "https://cdn.invalid/target.mp4")


if __name__ == "__main__":
    unittest.main()
