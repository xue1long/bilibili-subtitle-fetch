"""小红书 board（收藏夹）页采集的落盘与飞书同步端。

配套 `scripts/xhs_board_inject.js`：那段 JS 在页面里边滚边收，把结果 POST
到本模块的本地 HTTP 服务；本模块负责归一化、按 note-id 去重、落盘，以及
把结果按视频ID去重后 upsert 进飞书多维表格。

实测依据（2026-11-18，见 docs/metadata-pipeline.md）：
- board 卡片直接给出 11 个字段，不需要打开笔记详情；
- 去重只能用 note_id，data-index 会随收藏夹重排变化；
- 标题被平台截断到 40 字符，封面直链无需 Referer。

CLI::

    # 1) 启动接收服务（另一个终端/工具里注入并运行 xhs_board_inject.js）
    python scripts/xhs_board_collect.py serve --out .scratch/xhs_board_full.json

    # 2) 落盘后同步到飞书（按视频ID去重，已有行 update、新行 create）
    python scripts/xhs_board_collect.py push --notes .scratch/xhs_board_full.json \
        --base UGWbb... --table tblXXX
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


NOTE_URL = ("https://www.xiaohongshu.com/explore/{note_id}"
            "?xsec_token={token}&xsec_source=pc_user&source={source}")

# board 卡片能给出的字段；详情才有的 published_at / 正文 / 话题标签
# 明确不在此列，避免下游误以为可以基于它们做检索。
BOARD_FIELDS = (
    "note_id", "xsec_token", "title", "author", "author_id",
    "author_token", "cover", "cover_width", "cover_height",
    "likes", "is_video", "data_index",
)


def normalize_note(raw: dict) -> dict | None:
    """Trim a raw board card to the stable field set; drop rows without an id."""
    if not isinstance(raw, dict):
        return None
    note_id = str(raw.get("note_id") or "").strip()
    if not note_id:
        return None
    note = {name: raw.get(name) for name in BOARD_FIELDS}
    note["note_id"] = note_id
    for name in ("xsec_token", "title", "author", "cover"):
        value = note.get(name)
        note[name] = str(value).strip() if value is not None else ""
    return note


def dedupe_notes(notes: list[dict]) -> tuple[list[dict], int]:
    """Deduplicate by note_id, keeping the first occurrence."""
    seen = set()
    unique = []
    duplicates = 0
    for raw in notes:
        note = normalize_note(raw)
        if note is None:
            continue
        if note["note_id"] in seen:
            duplicates += 1
            continue
        seen.add(note["note_id"])
        unique.append(note)
    return unique, duplicates


def note_url(note: dict, source: str = "web_board_page") -> str:
    """Normalize to /explore/<id>, avoiding the two-segment board id ambiguity."""
    return NOTE_URL.format(
        note_id=note["note_id"],
        token=note.get("xsec_token", ""),
        source=source,
    )


def to_feishu_records(notes: list[dict]) -> list[dict]:
    """Map board notes to the「视频清单」field names."""
    records = []
    for note in notes:
        author = note.get("author") or ""
        records.append({
            "视频ID": note["note_id"],
            "标题": note.get("title") or "",
            "UP主": [author] if author else [],
            "来源链接": note_url(note),
            "平台": ["xiaohongshu"],
            "封面": note.get("cover") or "",
        })
    return records


class _CollectHandler(BaseHTTPRequestHandler):
    """Receive incremental POSTs from the injected page script."""

    server_version = "XhsBoardCollect/1.0"

    def do_POST(self):
        if self.path.split("?")[0] not in ("/collect", "/"):
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            self.send_error(400, "empty body")
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            incoming = payload.get("notes", [])
            if not isinstance(incoming, list):
                raise ValueError("notes must be an array")
        except (ValueError, UnicodeDecodeError) as exc:
            self.send_error(400, str(exc))
            return

        with self.server.lock:
            merged, duplicates = dedupe_notes(
                self.server.notes + incoming)
            self.server.notes = merged
            self.server.duplicates += duplicates
            self.server.output.write_text(
                json.dumps(merged, ensure_ascii=False, indent=1),
                encoding="utf-8")

        body = json.dumps({"total": len(merged),
                           "duplicates": self.server.duplicates},
                          ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        body = json.dumps({"total": len(self.server.notes),
                           "duplicates": self.server.duplicates},
                          ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        # Keep the terminal usable during a 3-minute scroll.
        pass


class CollectServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, output: Path, existing: list[dict] | None = None):
        super().__init__(address, _CollectHandler)
        self.output = Path(output)
        self.output.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.notes, self.duplicates = dedupe_notes(existing or [])
        self.output.write_text(
            json.dumps(self.notes, ensure_ascii=False, indent=1),
            encoding="utf-8")


def serve(output: Path, host: str = "127.0.0.1", port: int = 8899,
          existing: list[dict] | None = None) -> CollectServer:
    return CollectServer((host, port), output, existing)


def push_notes(notes: list[dict], base: str, table: str, *, cli=None,
               identity: str = "bot") -> dict:
    """Upsert board notes into Feishu, keyed by 视频ID (the note id)."""
    from feishu_base import FeishuBase

    base_client = cli or FeishuBase(base, identity=identity)
    return base_client.upsert_records(table, to_feishu_records(notes),
                                      key_field="视频ID")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    serve_parser = sub.add_parser("serve", help="接收页内脚本的 POST 并落盘")
    serve_parser.add_argument("--out", type=Path, required=True)
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8899)
    serve_parser.add_argument("--resume", type=Path,
                              help="已有的采集 JSON，续跑时先载入")

    push_parser = sub.add_parser("push", help="把采集结果 upsert 到飞书")
    push_parser.add_argument("--notes", type=Path, required=True)
    push_parser.add_argument("--base", required=True)
    push_parser.add_argument("--table", required=True)
    push_parser.add_argument("--identity", default="bot")

    args = parser.parse_args(argv)
    if args.command == "serve":
        existing = (json.loads(args.resume.read_text(encoding="utf-8"))
                    if args.resume else None)
        server = serve(args.out, args.host, args.port, existing)
        print(f"LISTENING http://{args.host}:{args.port}/collect -> {args.out}",
              flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            server.server_close()
        return 0

    notes = json.loads(args.notes.read_text(encoding="utf-8"))
    unique, duplicates = dedupe_notes(notes)
    summary = push_notes(unique, args.base, args.table, identity=args.identity)
    print(json.dumps({"input": len(notes), "unique": len(unique),
                      "duplicates": duplicates, **summary},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
