"""Cover-based one-line summary pipeline.

The 2026-11-18/19 full runs exposed three traps this module is built around:

1. Covers are identified by an explicit ``idx`` value, not by array position.
   One failed download (idx 205) shifted every later record_id by one and
   silently corrupted 75 rows. :func:`index_items` keys by ``idx`` so gaps
   are safe; never slice a manifest by ``idx - 1``.
2. Vision models do not read webp reliably. :func:`to_png` converts before
   the image is handed to a reviewer.
3. Not every row has usable evidence. :func:`classify_source` makes the
   fallback explicit: cover + title, title only, or skip.

Reviewing the contact sheet still needs a human/agent; this module covers
the deterministic parts: cover download, conversion, index-safe merge,
Feishu write-back and read-back verification.

Examples::

    python scripts/summary_pipeline.py download --items .scratch/manifest.json \
        --out .scratch/covers
    python scripts/summary_pipeline.py merge --store .scratch/summaries.json \
        --items .scratch/manifest.json --batch .scratch/batch_001.json
    python scripts/summary_pipeline.py write --store .scratch/summaries.json \
        --base UGWbb... --table tblXXX
    python scripts/summary_pipeline.py verify --store .scratch/summaries.json \
        --base UGWbb... --table tblXXX --platform bilibili
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from pathlib import Path

FIELD_TYPES = ("text", "select")

# Titles that carry no content clue: placeholder rows left by vanished videos.
_PLACEHOLDER_RE = re.compile(r"^(已失效视频|失效视频|视频不存在)$")
_BVID_RE = re.compile(r"^BV[0-9A-Za-z]+$")


def index_items(items: list[dict], id_field: str = "idx") -> dict[int, dict]:
    """Index items by their explicit id value, tolerating gaps and holes.

    This is the fix for the 2026-11-18 bug: ``items[idx - 1]`` is wrong as
    soon as one cover download fails and never makes it into the manifest.
    """
    indexed: dict[int, dict] = {}
    for item in items:
        try:
            key = int(item[id_field])
        except (KeyError, TypeError, ValueError):
            continue
        indexed[key] = item
    return indexed


def is_placeholder(title: str) -> bool:
    """True when the title gives no evidence for a summary."""
    text = (title or "").strip()
    return not text or bool(_PLACEHOLDER_RE.match(text)) or bool(_BVID_RE.match(text))


def classify_source(title: str, cover: str = "") -> str:
    """Return ``skip`` / ``title`` / ``cover+title`` for evidence selection."""
    if is_placeholder(title):
        return "skip"
    return "cover+title" if (cover or "").strip() else "title"


def download_cover(url: str, dest: Path, *, fetch=None) -> Path | None:
    """Download one cover to ``dest``; return None on any failure.

    ``fetch`` is injectable so tests never touch the network. The default
    uses stdlib urllib, which needs no third-party dependency.
    """
    url = (url or "").strip()
    if not url:
        return None
    if url.startswith("//"):
        url = "https:" + url
    elif url.startswith("http://"):
        url = "https://" + url[len("http://"):]
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        if fetch is not None:
            data = fetch(url)
        else:
            request = urllib.request.Request(
                url, headers={"User-Agent": "Mozilla/5.0",
                              "Referer": "https://www.bilibili.com/"})
            with urllib.request.urlopen(request, timeout=20) as response:
                data = response.read()
        if not data:
            return None
        dest.write_bytes(data)
        return dest
    except Exception:
        return None


def to_png(src: Path, dest: Path) -> Path:
    """Convert webp/jpeg to PNG; Pillow is imported lazily."""
    from PIL import Image

    with Image.open(src) as image:
        image.convert("RGB").save(dest, format="PNG")
    return dest


class SummaryStore:
    """Incremental ``{idx: {record_id, title, summary}}`` store on disk."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.data: dict[str, dict] = {}
        if self.path.exists():
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                self.data = {str(k): v for k, v in loaded.items()}

    def put(self, idx: int, record_id: str, title: str, summary: str,
            *, bvid: str = "") -> None:
        self.data[str(idx)] = {
            "record_id": record_id,
            "bvid": bvid,
            "title": title,
            "summary": summary.strip(),
        }

    def rows(self) -> list[dict]:
        """Feishu write shape: ``[{record_id, summary}, ...]``."""
        return [
            {"record_id": row["record_id"], "summary": row["summary"]}
            for row in self.data.values()
            if row.get("record_id") and row.get("summary")
        ]

    def expected(self) -> dict[str, str]:
        """Read-back shape: ``{record_id: expected_summary}``."""
        return {row["record_id"]: row["summary"] for row in self.rows()}

    def save(self) -> int:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=1),
                             encoding="utf-8")
        return len(self.data)


def merge_batch(store: SummaryStore, items: list[dict],
                batch: list[dict]) -> dict:
    """Merge ``[{idx, summary}]`` using idx *values*, never array positions.

    Returns ``{added, skipped, missing_item}`` so an out-of-range idx cannot
    silently attach a summary to the wrong record.
    """
    by_idx = index_items(items)
    report = {"added": 0, "skipped": 0, "missing_item": []}
    for entry in batch:
        try:
            idx = int(entry["idx"])
        except (KeyError, TypeError, ValueError):
            report["skipped"] += 1
            continue
        item = by_idx.get(idx)
        if item is None:
            report["missing_item"].append(idx)
            continue
        summary = str(entry.get("summary") or "").strip()
        if not summary:
            report["skipped"] += 1
            continue
        store.put(idx,
                  str(item.get("record_id") or ""),
                  str(item.get("title") or ""),
                  summary,
                  bvid=str(item.get("bvid") or ""))
        report["added"] += 1
    return report


def write_summaries(base, table_id: str, store: SummaryStore,
                    field: str = "摘要") -> int:
    """Batch-write the store's summaries through FeishuBase.update_records."""
    rows = store.rows()
    return base.update_records(table_id,
                               {row["record_id"]: {field: row["summary"]}
                                for row in rows})


def verify_summaries(base, table_id: str, store: SummaryStore,
                     *, field: str = "摘要",
                     filter_field: str | None = "平台",
                     filter_value: str | None = None,
                     **read_kwargs) -> dict:
    """Read back and compare every stored summary."""
    return base.verify_field(table_id, field, store.expected(),
                             filter_field=filter_field,
                             filter_value=filter_value,
                             **read_kwargs)


def _load_json(path: str) -> list[dict]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = data.get("items") or data.get("notes") or list(data.values())
    return data


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="封面摘要生成流水线")
    sub = parser.add_subparsers(dest="command", required=True)

    dl = sub.add_parser("download", help="下载封面并转 PNG")
    dl.add_argument("--items", required=True, help="含 idx/cover 的 JSON")
    dl.add_argument("--out", required=True, help="输出目录")
    dl.add_argument("--limit", type=int, default=0)

    mg = sub.add_parser("merge", help="把摘要批次按 idx 值合并进摘要库")
    mg.add_argument("--store", required=True)
    mg.add_argument("--items", required=True)
    mg.add_argument("--batch", required=True)

    wr = sub.add_parser("write", help="批量写入飞书摘要字段")
    wr.add_argument("--store", required=True)
    wr.add_argument("--base", required=True)
    wr.add_argument("--table", required=True)
    wr.add_argument("--field", default="摘要")

    vf = sub.add_parser("verify", help="回读校验飞书摘要字段")
    vf.add_argument("--store", required=True)
    vf.add_argument("--base", required=True)
    vf.add_argument("--table", required=True)
    vf.add_argument("--field", default="摘要")
    vf.add_argument("--platform", default=None)
    vf.add_argument("--concurrency", type=int, default=8)

    args = parser.parse_args(argv)

    if args.command == "download":
        items = _load_json(args.items)
        if args.limit:
            items = items[:args.limit]
        out = Path(args.out)
        ok = failed = converted = 0
        for item in items:
            idx = item.get("idx")
            url = item.get("cover") or item.get("raw")
            if idx is None or not url:
                failed += 1
                continue
            raw_path = out / f"{int(idx):04d}.img"
            png_path = out / f"{int(idx):04d}.png"
            if download_cover(url, raw_path) is None:
                failed += 1
                continue
            ok += 1
            try:
                to_png(raw_path, png_path)
                converted += 1
            except Exception:
                failed += 1
        print(f"COVER downloaded={ok} converted={converted} failed={failed}")
        return 0

    if args.command == "merge":
        store = SummaryStore(Path(args.store))
        report = merge_batch(store, _load_json(args.items),
                             _load_json(args.batch))
        total = store.save()
        print(f"MERGED added={report['added']} skipped={report['skipped']} "
              f"missing_item={len(report['missing_item'])} total={total}")
        return 0

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from feishu_base import FeishuBase

    base = FeishuBase(args.base)
    store = SummaryStore(Path(args.store))

    if args.command == "write":
        count = write_summaries(base, args.table, store, field=args.field)
        print(f"WROTE {count}")
        return 0

    report = verify_summaries(base, args.table, store, field=args.field,
                              filter_value=args.platform,
                              concurrency=args.concurrency)
    print("VERIFIED checked={checked} mismatch={mismatch} missing={missing}".format(
        **report))
    return 0 if report["mismatch"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
