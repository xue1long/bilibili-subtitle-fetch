"""Collect platform metadata, write it to the state store, and sync a Feishu base.

The pipeline is: local sources -> browser fallback -> SQLite -> Feishu.

Every stage is idempotent, so the command can be re-run after new data
arrives. Rows that already have a value are left alone unless ``--overwrite``
is passed.

Examples::

    # show what the local sources can cover
    python scripts/sync_metadata.py --report

    # write collected metadata into SQLite + manifest
    python scripts/sync_metadata.py --apply

    # push the result into a Feishu base
    python scripts/sync_metadata.py --apply --feishu-token UGWbb... --feishu-table tblXXX
"""
from __future__ import annotations

import argparse
import json
import sys
from contextlib import closing
from pathlib import Path

from platform_meta import collect, default_appdata, merge, write_json
from runtime_paths import project_root
from storage.platform_manifest import manifest_path
from storage.sqlite_store import SQLiteStore


PLATFORMS = ("bilibili", "douyin", "xiaohongshu")

# Feishu single-select options are fixed at table creation; keep them in sync.
FEISHU_SELECTS = {
    "平台": ["bilibili", "douyin", "xiaohongshu"],
    "任务状态": ["native_success", "success", "asr_success", "failed",
                 "paused", "backfilled"],
}

FEISHU_FIELDS = [
    {"name": "视频ID", "type": "text"},
    {"name": "平台", "type": "select", "multiple": False,
     "options": [{"name": n} for n in FEISHU_SELECTS["平台"]]},
    {"name": "标题", "type": "text"},
    {"name": "UP主", "type": "text"},
    {"name": "任务状态", "type": "select", "multiple": False,
     "options": [{"name": n} for n in FEISHU_SELECTS["任务状态"]]},
    {"name": "来源链接", "type": "url"},
    {"name": "上传时间", "type": "datetime",
     "style": {"format": "yyyy/MM/dd"}},
    {"name": "下载时间", "type": "datetime",
     "style": {"format": "yyyy/MM/dd HH:mm"}},
]


def _platform_url(platform: str, video_id: str) -> str:
    if platform == "bilibili":
        return f"https://www.bilibili.com/video/{video_id}"
    if platform == "douyin":
        return f"https://www.douyin.com/video/{video_id}"
    return f"https://www.xiaohongshu.com/explore/{video_id}"


def _resolve_url(platform: str, video_id: str, record: dict) -> str:
    """Prefer a collected share URL over a reconstructed one."""
    source = (record.get("source_url") or "").strip()
    if source.startswith("http"):
        return source
    return _platform_url(platform, video_id)


def build_rows(platform: str, meta: dict[str, dict],
               store: SQLiteStore) -> list[dict]:
    """Turn collected metadata into Feishu-shaped rows."""
    jobs = store.jobs(platform)
    rows: list[dict] = []
    for video_id, record in meta.items():
        row = {"视频ID": video_id, "平台": [platform]}
        if record.get("title"):
            row["标题"] = record["title"][:200]
        if record.get("uploader"):
            row["UP主"] = record["uploader"][:80]
        # A collected share URL is more useful than a reconstructed one.
        row["来源链接"] = _resolve_url(platform, video_id, record)
        if record.get("published_at"):
            row["上传时间"] = record["published_at"]
        if record.get("downloaded_at"):
            row["下载时间"] = record["downloaded_at"]
        job = jobs.get(video_id)
        if job and job.get("status"):
            row["任务状态"] = [job["status"]]
        rows.append(row)
    return rows


def apply_to_store(root: Path, meta_by_platform: dict[str, dict],
                   overwrite: bool = False) -> dict:
    """Write collected metadata into SQLite and the platform manifest."""
    store = SQLiteStore(root / "data" / "subtitle_tasks.db")
    counts = {"title": 0, "uploader": 0, "url": 0}
    with closing(store.connect()) as db:
        for platform, meta in meta_by_platform.items():
            for video_id, record in meta.items():
                title = record.get("title", "")
                uploader = record.get("uploader", "")
                url = _resolve_url(platform, video_id, record)
                condition = "" if overwrite else (
                    " AND (title IS NULL OR title='')")
                counts["title"] += db.execute(
                    "UPDATE videos SET title=? WHERE platform=? AND video_id=?"
                    + condition,
                    (title, platform, video_id)).rowcount
                condition = "" if overwrite else (
                    " AND (uploader IS NULL OR uploader='')")
                counts["uploader"] += db.execute(
                    "UPDATE videos SET uploader=? WHERE platform=? AND video_id=?"
                    + condition,
                    (uploader, platform, video_id)).rowcount
                counts["url"] += db.execute(
                    "UPDATE videos SET source_url=? WHERE platform=? AND video_id=? "
                    "AND (source_url IS NULL OR source_url='' "
                    "OR source_url NOT LIKE 'http%')",
                    (url, platform, video_id)).rowcount
        db.commit()

    manifest = manifest_path(root)
    if manifest.exists():
        data = json.loads(manifest.read_text(encoding="utf-8"))
        for platform, meta in meta_by_platform.items():
            bucket = data.setdefault("sources", {}).setdefault(platform, {})
            for video_id, record in meta.items():
                entry = bucket.setdefault(video_id, {"video_id": video_id})
                if record.get("title"):
                    entry["title"] = record["title"]
                if record.get("uploader"):
                    entry["uploader"] = record["uploader"]
                entry["source_url"] = _resolve_url(platform, video_id, record)
                entry["url"] = entry["source_url"]
        write_json(manifest, data)
    return counts


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="收集各平台元数据并同步到状态库与飞书多维表格")
    parser.add_argument("--root", type=Path, default=project_root())
    parser.add_argument("--report", action="store_true",
                        help="只统计本地源能覆盖多少，不写任何数据")
    parser.add_argument("--apply", action="store_true",
                        help="把收集到的元数据写入 SQLite 与 manifest")
    parser.add_argument("--overwrite", action="store_true",
                        help="覆盖已有值（默认只填空列）")
    parser.add_argument("--cache", type=Path, default=None,
                        help="把合并结果写到该 JSON 文件")
    parser.add_argument("--feishu-token", default="",
                        help="飞书 Base token；提供后同步到多维表格")
    parser.add_argument("--feishu-table", default="",
                        help="飞书多维表格 table id")
    parser.add_argument("--feishu-identity", choices=("user", "bot"),
                        default="user")
    args = parser.parse_args(argv)

    sources = collect(args.root)
    print(f"抖珍藏本地库: {default_appdata()}")
    for platform in PLATFORMS:
        print(f"  {platform:14} {len(sources.get(platform, {})):5} 条")

    merged = {platform: merge(sources.get(platform, {}))
              for platform in PLATFORMS}
    if args.cache:
        write_json(args.cache, merged)

    if args.report:
        return 0
    if not args.apply and not args.feishu_token:
        parser.error("需要 --apply 或 --feishu-token 之一")
        return 2

    if args.apply:
        counts = apply_to_store(args.root, merged, overwrite=args.overwrite)
        print(f"状态库更新: title {counts['title']}, "
              f"uploader {counts['uploader']}, url {counts['url']}")

    if args.feishu_token:
        if not args.feishu_table:
            parser.error("--feishu-token 需要同时提供 --feishu-table")
            return 2
        from feishu_base import FeishuBase
        base = FeishuBase(args.feishu_token, identity=args.feishu_identity)
        store = SQLiteStore(args.root / "data" / "subtitle_tasks.db")
        rows: list[dict] = []
        skipped_new = 0
        for platform in PLATFORMS:
            known = set(store.jobs(platform))
            platform_meta = merged.get(platform, {})
            # The base mirrors the collected inventory. A local source may
            # know about videos this project never fetched (no media file),
            # and those must not be added.
            unknown = set(platform_meta) - known
            skipped_new += len(unknown)
            subset = {vid: rec for vid, rec in platform_meta.items()
                      if vid in known}
            rows.extend(build_rows(platform, subset, store))
        summary = base.upsert_records(args.feishu_table, rows)
        print(f"飞书同步: 更新 {summary['updated']}，新增 {summary['created']}，"
              f"跳过 {summary['skipped']}"
              + (f"（未采集的视频 {skipped_new} 条已忽略）" if skipped_new else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
