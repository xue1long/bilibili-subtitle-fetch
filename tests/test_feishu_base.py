import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

from feishu_base import FeishuBase, FeishuError


class FakeFeishuBase(FeishuBase):
    """Records the CLI invocations instead of running them."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.calls = []
        self.responses = {}
        self.payloads = []
        self.v3_pages = {}
        self.call_envs = []
        self.option_pages = {}
        self.writes = []

    def _run(self, args, *, check=True, env=None):
        # Mirror the real _run: append the identity flag before recording.
        command = [self.cli] + args + ["--as", self.identity]
        self.calls.append(command)
        self.call_envs.append(env)
        if "--json" in args:
            raw = args[args.index("--json") + 1]
            if raw.startswith("@"):
                path = Path(raw[1:])
                self.payloads.append(json.loads(path.read_text(encoding="utf-8")))
        if args[:2] == ["api", "GET"]:
            params = json.loads(args[args.index("--params") + 1])
            if "/options" in args[2]:
                return json.dumps(self.option_pages.get(params.get("offset"), {
                    "ok": True,
                    "data": {"items": [], "total": 0},
                }))
            return json.dumps(self.v3_pages.get(params.get("offset"), {
                "ok": True,
                "data": {"fields": [], "data": [], "record_id_list": [],
                         "has_more": False},
            }))
        if args[:2] == ["api", "PUT"]:
            raw = args[args.index("--data") + 1]
            if raw.startswith("@"):
                self.writes.append(json.loads(Path(raw[1:]).read_text(
                    encoding="utf-8")))
            return json.dumps({"ok": True, "data": {}})
        payload = self.responses.get(args[1], {"ok": True, "data": {}})
        if check and not payload.get("ok"):
            raise FeishuError(f"{args[0]} failed")
        return json.dumps(payload)


class V3RecordsTest(unittest.TestCase):
    """The raw v3 endpoint is column-shaped and pages at 20 rows."""

    @staticmethod
    def page(fields, rows, ids, has_more=False):
        return {"ok": True, "data": {"fields": fields, "data": rows,
                                     "record_id_list": ids,
                                     "has_more": has_more}}

    def setUp(self):
        self.base = FakeFeishuBase("TOKEN")
        self.fields = ["标题", "平台", "摘要"]

    def test_decode_v3_page_zips_columns_with_names(self):
        page = self.page(self.fields, [["甲", ["bilibili"], "摘要甲"]], ["r1"])
        decoded = self.base.decode_v3_page(page["data"])
        self.assertEqual(decoded, [{"record_id": "r1", "标题": "甲",
                                    "平台": ["bilibili"], "摘要": "摘要甲"}])

    def test_one_value_unwraps_select_and_text_arrays(self):
        self.assertEqual(self.base.one_value(["bilibili"]), "bilibili")
        self.assertEqual(self.base.one_value([]), None)
        self.assertEqual(self.base.one_value("plain"), "plain")

    def test_list_records_v3_fetches_pages_in_parallel(self):
        self.base.v3_pages[0] = self.page(
            self.fields, [["甲", ["bilibili"], "甲摘要"]], ["r1"], True)
        self.base.v3_pages[20] = self.page(
            self.fields, [["乙", ["bilibili"], "乙摘要"],
                          ["丙", ["douyin"], "丙摘要"]], ["r2", "r3"])
        rows = self.base.list_records_v3("tblX", concurrency=3)
        self.assertEqual([r["record_id"] for r in rows], ["r1", "r2", "r3"])
        offsets = [json.loads(c[c.index("--params") + 1])["offset"]
                   for c in self.base.calls if len(c) > 1 and c[1] == "api"]
        self.assertIn(0, offsets)
        self.assertIn(20, offsets)
        self.assertIn(60, offsets)  # speculative batch, empty page

    def test_list_records_v3_sets_msys_no_pathconv(self):
        self.base.v3_pages[0] = self.page(self.fields, [], [])
        self.base.list_records_v3("tblX", concurrency=2)
        self.assertEqual(self.base.call_envs[0]["MSYS_NO_PATHCONV"], "1")

    def test_list_records_v3_dedups_record_ids(self):
        duplicate = self.page(self.fields, [["甲", ["bilibili"], None]], ["r1"])
        self.base.v3_pages[0] = self.page(self.fields, [], [], True)
        self.base.v3_pages[20] = duplicate
        rows = self.base.list_records_v3("tblX", concurrency=1)
        self.assertEqual(len(rows), 1)

    def test_list_records_v3_rejects_zero_concurrency(self):
        with self.assertRaises(ValueError):
            self.base.list_records_v3("tblX", concurrency=0)

    def test_compare_field_filters_and_reports_missing(self):
        rows = [
            {"record_id": "r1", "平台": ["bilibili"], "摘要": " 正确 "},
            {"record_id": "r2", "平台": ["bilibili"], "摘要": "旧内容"},
            {"record_id": "r3", "平台": ["douyin"], "摘要": "无关"},
        ]
        report = self.base.compare_field(
            rows, "摘要", {"r1": "正确", "r2": "新内容", "r9": "缺行"},
            filter_field="平台", filter_value="bilibili")
        self.assertEqual(report["total"], 2)
        self.assertEqual(report["checked"], 2)
        self.assertEqual(report["mismatch"], 1)
        self.assertEqual(report["missing"], 1)
        self.assertEqual(report["examples"][0]["record_id"], "r2")

    def test_verify_field_combines_read_and_compare(self):
        self.base.v3_pages[0] = self.page(
            self.fields, [["甲", ["bilibili"], "摘要甲"]], ["r1"])
        report = self.base.verify_field(
            "tblX", "摘要", {"r1": "摘要甲"},
            filter_field="平台", filter_value="bilibili", concurrency=2)
        self.assertEqual(report["checked"], 1)
        self.assertEqual(report["mismatch"], 0)


class FieldOptionsTest(unittest.TestCase):
    """Select options need full paging and merge-before-PUT."""

    def setUp(self):
        self.base = FakeFeishuBase("TOKEN")
        self.base.responses["+field-list"] = {
            "ok": True,
            "data": {"fields": [{"id": "fldSEL", "name": "UP主",
                                 "type": "select", "multiple": False}]},
        }

    def test_list_field_options_pages_until_total(self):
        self.base.option_pages[0] = {
            "ok": True,
            "data": {"items": [{"name": "甲"}], "total": 3},
        }
        self.base.option_pages[1] = {
            "ok": True,
            "data": {"items": [{"name": "乙"}, {"name": "丙"}], "total": 3},
        }
        options = self.base.list_field_options("tblX", "UP主", page_size=1)
        self.assertEqual([o["name"] for o in options], ["甲", "乙", "丙"])

    def test_ensure_field_options_merges_without_dropping_existing(self):
        self.base.option_pages[0] = {
            "ok": True,
            "data": {"items": [{"name": "甲", "hue": "blue"}], "total": 1},
        }
        result = self.base.ensure_field_options("tblX", "UP主", ["乙", "甲"])
        self.assertEqual(result, {"field_id": "fldSEL", "before": 1,
                                  "after": 2, "added": 1})
        body = self.base.writes[-1]
        self.assertEqual([o["name"] for o in body["options"]], ["甲", "乙"])
        # hue/lightness are rejected by the API; only name may be submitted.
        self.assertTrue(all(set(option) == {"name"}
                            for option in body["options"]))
        self.assertEqual(body["type"], "select")

    def test_api_write_sets_msys_no_pathconv_and_uses_file_body(self):
        self.base.option_pages[0] = {
            "ok": True, "data": {"items": [], "total": 0}}
        self.base.ensure_field_options("tblX", "UP主", ["甲"])
        write_call = [c for c in self.base.calls if c[2] == "PUT"][0]
        self.assertTrue(write_call[write_call.index("--data") + 1].startswith("@"))
        env = [e for c, e in zip(self.base.calls, self.base.call_envs)
               if c[2] == "PUT"][0]
        self.assertEqual(env["MSYS_NO_PATHCONV"], "1")

    def test_ensure_field_options_rejects_unknown_field(self):
        with self.assertRaisesRegex(Exception, "field not found"):
            self.base.ensure_field_options("tblX", "不存在", ["甲"])


class FeishuBaseCommandTest(unittest.TestCase):
    def setUp(self):
        self.base = FakeFeishuBase("TOKEN", batch_size=2)

    def test_passes_identity_flag(self):
        self.base._run(["base", "+table-list"])
        self.assertEqual(self.base.calls[-1][-2:], ["--as", "user"])

    def test_batches_large_payloads(self):
        self.base.update_records("tblX", {f"rec{i}": {"标题": str(i)}
                                          for i in range(5)})
        writes = [c for c in self.base.calls if "+record-batch-update" in c]
        self.assertEqual(len(writes), 3)  # 5 records, batch size 2
        first = self.base.payloads[0]["update_records"]
        self.assertEqual(len(first), 2)

    def test_payload_is_passed_via_file_reference(self):
        # Inline JSON blows the Windows command-line limit on long titles.
        self.base.create_records("tblX", [{"视频ID": "BV1"}])
        call = [c for c in self.base.calls if "+record-batch-create" in c][0]
        raw = call[call.index("--json") + 1]
        self.assertTrue(raw.startswith("@"))

    def test_create_records_uses_create_records_key(self):
        self.base.create_records("tblX", [{"视频ID": "BV1"}])
        self.assertEqual(self.base.payloads[0]["create_records"],
                         [{"视频ID": "BV1"}])

    def test_update_records_uses_a_map_not_an_array(self):
        # The API rejects an array of pairs; it must be {record_id: fields}.
        self.base.update_records("tblX", {"rec1": {"标题": "甲"},
                                          "rec2": {"标题": "乙"}})
        self.assertEqual(self.base.payloads[0]["update_records"],
                         {"rec1": {"标题": "甲"}, "rec2": {"标题": "乙"}})

    def test_ensure_field_skips_existing(self):
        self.base.responses["+field-list"] = {
            "ok": True, "data": {"fields": [{"name": "标题", "type": "text"}]}}
        existing = self.base.ensure_field("tblX", "标题", "text")
        self.assertEqual(existing["name"], "标题")
        self.assertFalse([c for c in self.base.calls if "+field-create" in c])

    def test_ensure_field_creates_when_missing(self):
        self.base.responses["+field-list"] = {
            "ok": True, "data": {"fields": []}}
        self.base.ensure_field("tblX", "上传时间", "datetime",
                               style={"format": "yyyy/MM/dd"})
        call = [c for c in self.base.calls if "+field-create" in c][0]
        body = json.loads(call[call.index("--json") + 1])
        self.assertEqual(body["name"], "上传时间")
        self.assertEqual(body["type"], "datetime")
    def test_upsert_splits_update_and_create(self):
        # Simulate the mapping directly to avoid touching the filesystem.
        self.base.record_id_map = lambda table_id, key_field="视频ID", **kw: {
            "BV1": "rec1"}
        summary = self.base.upsert_records("tblX", [
            {"视频ID": "BV1", "标题": "更新"},
            {"视频ID": "BV2", "标题": "新增"},
        ])
        self.assertEqual(summary["updated"], 1)
        self.assertEqual(summary["created"], 1)
        self.assertEqual(summary["skipped"], 0)

    def test_upsert_skips_rows_without_key(self):
        self.base.record_id_map = lambda table_id, key_field="视频ID", **kw: {}
        summary = self.base.upsert_records("tblX", [{"标题": "无主键"}])
        self.assertEqual(summary["skipped"], 1)
        self.assertEqual(summary["created"], 0)


if __name__ == "__main__":
    unittest.main()
