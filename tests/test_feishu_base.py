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

    def _run(self, args, *, check=True):
        # Mirror the real _run: append the identity flag before recording.
        command = [self.cli] + args + ["--as", self.identity]
        self.calls.append(command)
        # The payload arrives as "@<path>"; read it back so assertions can
        # inspect the real body.
        if "--json" in args:
            raw = args[args.index("--json") + 1]
            if raw.startswith("@"):
                path = Path(raw[1:])
                self.payloads.append(json.loads(path.read_text(encoding="utf-8")))
        payload = self.responses.get(args[1], {"ok": True, "data": {}})
        if check and not payload.get("ok"):
            raise FeishuError(f"{args[0]} failed")
        return json.dumps(payload)


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
