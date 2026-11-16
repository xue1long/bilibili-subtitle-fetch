import base64
import gzip
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

from platform_meta import (
    collect,
    decode_appdata_file,
    merge,
    parse_frontmatter,
    read_bilibili_frontmatter,
    read_download_list,
    read_douzh_videos,
    read_videos_manifest,
    read_xiaohongshu_json,
)


def _write_appdata(path: Path, name: str, payload: dict) -> None:
    raw = gzip.compress(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    encoded = base64.b64encode(raw).decode("ascii")
    (path / name).write_text(
        f'window.{name[:-3]}_base64="{encoded}"', encoding="utf-8")


class ParseFrontmatterTest(unittest.TestCase):
    def test_reads_simple_pairs(self):
        text = "---\ntitle: 我的标题\nuploader: 作者\nbvid: BV1xx\n---\n正文"
        fields = parse_frontmatter(text)
        self.assertEqual(fields["title"], "我的标题")
        self.assertEqual(fields["uploader"], "作者")
        self.assertEqual(fields["bvid"], "BV1xx")

    def test_strips_quotes(self):
        text = "---\nvideo_published_at: '2026-02-13'\n---\n"
        self.assertEqual(parse_frontmatter(text)["video_published_at"],
                         "2026-02-13")

    def test_returns_empty_without_frontmatter(self):
        self.assertEqual(parse_frontmatter("# 标题\n正文"), {})

    def test_ignores_nested_list_items(self):
        text = "---\ntags:\n- a\n- b\ntitle: T\n---\n"
        fields = parse_frontmatter(text)
        self.assertNotIn("- a", fields)
        self.assertEqual(fields["title"], "T")


class BilibiliFrontmatterTest(unittest.TestCase):
    def test_reads_both_bvid_and_bv_dialects(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / "BVAAA.md").write_text(
                "---\ntitle: 甲\nbvid: BVAAA\nuploader: 上\n"
                "video_published_at: 2026-01-02\n---\n", encoding="utf-8")
            (directory / "BVBBB.md").write_text(
                "---\nuid: 20260403-36F6\ntitle: 乙\nbv: BVBBB\n"
                "uploader: 下\nvideo_published_at: '2026-03-04'\n---\n",
                encoding="utf-8")
            (directory / "BVCCC.md").write_text("# 无 frontmatter\n",
                                               encoding="utf-8")
            result = read_bilibili_frontmatter(directory)
        self.assertEqual(set(result), {"BVAAA", "BVBBB"})
        self.assertEqual(result["BVAAA"]["title"], "甲")
        self.assertEqual(result["BVBBB"]["title"], "乙")
        self.assertEqual(result["BVBBB"]["published_at"], "2026-03-04")
        self.assertEqual(result["BVAAA"]["uploader"], "上")

    def test_missing_directory_returns_empty(self):
        result = read_bilibili_frontmatter(Path("does-not-exist-xyz"))
        self.assertEqual(result, {})


class DownloadListTest(unittest.TestCase):
    def test_reads_downloaded_at(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "download_list.json"
            path.write_text(json.dumps({
                "records": {
                    "BV1": {"status": "success",
                            "downloaded_at": "2026-09-21T15:37:47"},
                    "BV2": {"status": "failed"},
                }}), encoding="utf-8")
            result = read_download_list(path)
        self.assertEqual(result["BV1"]["downloaded_at"], "2026-09-21 15:37:47")
        self.assertNotIn("BV2", result)

    def test_missing_file_returns_empty(self):
        self.assertEqual(read_download_list(Path("nope-xyz.json")), {})


class VideosManifestTest(unittest.TestCase):
    def test_reads_titles(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "videos_manifest.json"
            path.write_text(json.dumps({
                "videos": {
                    "BV1": {"title": "标题", "uploader": "作者"},
                    "BV2": {"title": ""},
                }}), encoding="utf-8")
            result = read_videos_manifest(path)
        self.assertEqual(result["BV1"]["title"], "标题")
        self.assertNotIn("title", result["BV2"])


class DouzhVideosTest(unittest.TestCase):
    def test_decodes_gzip_base64_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            _write_appdata(directory, "db_videos.js", {
                "7318230681203510591": {
                    "authorId": "4211606622577508",
                    "createTime": 1700000000,
                    "diggCount": 8937,
                }})
            _write_appdata(directory, "db_authors.js", {
                "4211606622577508": {"nicknames": ["旧名", "新名"]}})
            _write_appdata(directory, "db_texts.js", {
                "7318230681203510591": "第一行标题\n第二行正文"})

            videos = decode_appdata_file(directory / "db_videos.js")
            self.assertIn("7318230681203510591", videos)

            result = read_douzh_videos(directory)
        entry = result["7318230681203510591"]
        self.assertEqual(entry["title"], "第一行标题")
        self.assertEqual(entry["uploader"], "新名")
        self.assertEqual(entry["like_count"], 8937)
        self.assertTrue(entry["published_at"].startswith("2023-"))

    def test_invalid_payload_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "db_videos.js"
            path.write_text("window.x_base64=", encoding="utf-8")
            with self.assertRaises(ValueError):
                decode_appdata_file(path)


class XiaohongshuJsonTest(unittest.TestCase):
    def test_reads_plain_array_and_state_wrapper(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / "board.json").write_text(json.dumps([
                {"noteId": "n1", "title": "标题一", "author": "作者一",
                 "url": "https://www.xiaohongshu.com/board/b/n1?xsec_token=T",
                 "likedCount": "12", "type": "video"}]), encoding="utf-8")
            (directory / "board.state.json").write_text(json.dumps({
                "board_id": "b",
                "notes": [{"noteId": "n2", "title": "标题二", "author": "作者二",
                           "url": "https://www.xiaohongshu.com/board/b/n2",
                           "collectedCount": "3"}]}), encoding="utf-8")
            result = read_xiaohongshu_json(directory)
        self.assertEqual(set(result), {"n1", "n2"})
        self.assertEqual(result["n1"]["title"], "标题一")
        self.assertEqual(result["n1"]["uploader"], "作者一")
        self.assertEqual(result["n1"]["like_count"], "12")
        self.assertEqual(result["n1"]["note_type"], "video")
        self.assertTrue(result["n1"]["source_url"].startswith("https://"))
        self.assertEqual(result["n2"]["uploader"], "作者二")
        self.assertEqual(result["n2"]["collected_count"], "3")

    def test_ignores_unreadable_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / "broken.json").write_text("{not json",
                                                    encoding="utf-8")
            (directory / "other.json").write_text(json.dumps({"a": 1}),
                                                  encoding="utf-8")
            result = read_xiaohongshu_json(directory)
        self.assertEqual(result, {})

    def test_missing_directory_returns_empty(self):
        self.assertEqual(read_xiaohongshu_json(Path("nope-xyz")), {})


class MergeTest(unittest.TestCase):
    def test_later_source_wins_and_empty_never_overwrites(self):
        merged = merge(
            {"a": {"title": "旧", "uploader": "甲"}},
            {"a": {"title": "新", "uploader": ""}, "b": {"title": "乙"}},
        )
        self.assertEqual(merged["a"]["title"], "新")
        self.assertEqual(merged["a"]["uploader"], "甲")
        self.assertEqual(merged["b"]["title"], "乙")


class CollectTest(unittest.TestCase):
    def test_collect_returns_every_platform(self):
        result = collect(Path("."))
        self.assertEqual(set(result),
                         {"bilibili", "douyin", "xiaohongshu"})


if __name__ == "__main__":
    unittest.main()
