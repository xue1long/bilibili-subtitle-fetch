import json
import sys
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

from xhs_board_collect import (  # noqa: E402
    CollectServer,
    dedupe_notes,
    note_url,
    normalize_note,
    push_notes,
    to_feishu_records,
)


SAMPLE = {
    "note_id": "6a1d3ad00000000001012abc",
    "xsec_token": "ABCxyz",
    "title": "  测试标题  ",
    "author": "  某作者 ",
    "cover": "  http://sns-img.xhscdn.com/a.webp  ",
}


class BoardNormalizeTest(unittest.TestCase):
    def test_normalize_strips_text_and_drops_rows_without_id(self):
        note = normalize_note(SAMPLE)
        self.assertEqual(note["note_id"], SAMPLE["note_id"])
        self.assertEqual(note["title"], "测试标题")
        self.assertEqual(note["author"], "某作者")
        self.assertEqual(note["cover"], "http://sns-img.xhscdn.com/a.webp")
        self.assertIsNone(normalize_note({"title": "无 id"}))
        self.assertIsNone(normalize_note(None))

    def test_dedupe_uses_note_id_not_data_index(self):
        first = dict(SAMPLE, data_index="214")
        second = dict(SAMPLE, data_index="250")  # same note, reordered board
        other = dict(SAMPLE, note_id="different", data_index="214")
        unique, duplicates = dedupe_notes([first, second, other])
        self.assertEqual(len(unique), 2)
        self.assertEqual(duplicates, 1)
        self.assertEqual(unique[0]["data_index"], "214")

    def test_note_url_uses_explore_and_keeps_token(self):
        url = note_url(normalize_note(SAMPLE))
        self.assertTrue(url.startswith(
            "https://www.xiaohongshu.com/explore/" + SAMPLE["note_id"]))
        self.assertIn("xsec_token=ABCxyz", url)
        self.assertIn("xsec_source=pc_user", url)
        self.assertNotIn("/board/", url)

    def test_feishu_records_map_platform_and_select_arrays(self):
        note = normalize_note(SAMPLE)
        records = to_feishu_records([note])
        record = records[0]
        self.assertEqual(record["视频ID"], SAMPLE["note_id"])
        self.assertEqual(record["平台"], ["xiaohongshu"])
        self.assertEqual(record["UP主"], ["某作者"])
        self.assertTrue(record["封面"].startswith("http"))
        self.assertEqual(set(record), {"视频ID", "标题", "UP主",
                                       "来源链接", "平台", "封面"})

    def test_empty_author_becomes_empty_select(self):
        note = normalize_note(dict(SAMPLE, author=""))
        self.assertEqual(to_feishu_records([note])[0]["UP主"], [])

    def test_push_notes_passes_note_id_as_key_field(self):
        class FakeFeishu:
            def __init__(self):
                self.calls = []

            def upsert_records(self, table, records, *, key_field):
                self.calls.append((table, records, key_field))
                return {"updated": 1, "created": 0, "skipped": 0}

        fake = FakeFeishu()
        summary = push_notes([normalize_note(SAMPLE)], "BASE", "TABLE", cli=fake)
        table, records, key_field = fake.calls[0]
        self.assertEqual(table, "TABLE")
        self.assertEqual(key_field, "视频ID")
        self.assertEqual(records[0]["视频ID"], SAMPLE["note_id"])
        self.assertEqual(summary["updated"], 1)


class CollectServerTest(unittest.TestCase):
    def test_post_merges_by_note_id_and_persists(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "notes.json"
            server = CollectServer(("127.0.0.1", 0), output)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                port = server.server_address[1]
                endpoint = f"http://127.0.0.1:{port}/collect"
                for notes in ([dict(SAMPLE)],
                              [dict(SAMPLE), dict(SAMPLE, note_id="second")]):
                    payload = json.dumps({"notes": notes}).encode("utf-8")
                    request = urllib.request.Request(
                        endpoint, data=payload,
                        headers={"Content-Type": "application/json"})
                    with urllib.request.urlopen(request, timeout=5) as response:
                        body = json.loads(response.read().decode("utf-8"))
                self.assertEqual(body["total"], 2)
                self.assertEqual(body["duplicates"], 1)
                saved = json.loads(output.read_text(encoding="utf-8"))
                self.assertEqual([n["note_id"] for n in saved],
                                 [SAMPLE["note_id"], "second"])
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

    def test_resume_loads_existing_notes(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "notes.json"
            existing = [dict(SAMPLE)]
            server = CollectServer(("127.0.0.1", 0), output, existing)
            try:
                self.assertEqual(len(server.notes), 1)
                self.assertIn(SAMPLE["note_id"], output.read_text(encoding="utf-8"))
            finally:
                server.server_close()


if __name__ == "__main__":
    unittest.main()
