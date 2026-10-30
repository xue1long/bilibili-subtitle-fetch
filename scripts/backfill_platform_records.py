"""Backfill existing Douyin/Xiaohongshu outputs into the multi-platform state.

The pre-existing folders under ``10_raw/02_抖音视频笔记`` and
``10_raw/03_小红书收藏夹`` already contain downloaded media and notes that were
written without registering them in ``data/videos_manifest.json`` /
``data/subtitle_tasks.db``. This script scans those folders, parses the note ids
from file names, and writes one record per file to:

  - ``data/platform_videos_manifest.json`` (with status="backfilled")
  - ``data/subtitle_tasks.db`` (jobs.status="backfilled")

It is idempotent: rerunning does not duplicate existing rows. Files whose ids are
< but already map to a different existing record are still inserted; this is
expected when backfilling an unconstrained set of pre-existing artefacts.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from runtime_paths import project_root
from storage.platform_manifest import (
    SUPPORTED_PLATFORMS,
    manifest_path,
    mark_completed,
    upsert,
)
from storage.sqlite_store import SQLiteStore


DOuyin_DIR = "02_抖音视频笔记"
XHS_DIRS = ("03_小红书收藏夹", "03_小红书")

# Notes named like ``6a322d530000000016026183.md`` (24 hex). Match the longest
# common id format; fall back to the bare filename if it cannot be extracted.
PY_ID_RE = re.compile(r"^([0-9a-f]{16,32})$")
DOuyin_FILE_RE = re.compile(r"^(\d{15,21})\.(md|mp4)$")


def _parse_douyin_entries(raw_dir: Path):
    """Scan the douyin folder. The .md file is canonical (one record per id); a
    companion .mp4 (or .mp4-only orphan) is recorded as an asset reference.
    """
    entries = []
    if not raw_dir.exists():
        return entries

    by_id: dict[str, dict] = {}
    for path in sorted(raw_dir.iterdir()):
        if not path.is_file():
            continue
        match = DOuyin_FILE_RE.match(path.name)
        if not match:
            continue
        video_id = match.group(1)
        suffix = path.suffix.lower().lstrip(".")
        entry = by_id.setdefault(video_id, {
            "video_id": video_id,
            "source_type": "backfill_douyin",
            "source_path": None,
            "assets": [],
            "asset_count": 0,
        })
        if suffix == "md":
            entry["source_path"] = str(path)
        else:
            entry["assets"].append(f"{video_id}.{suffix}")
    for entry in by_id.values():
        entry["asset_count"] = len(entry["assets"])
        if entry["source_path"]:
            entries.append(entry)
        elif entry["assets"]:
            # Orphan .mp4 (no .md). Use mp4 as source for the manifest but still
            # register a record so the id is not re-downloaded.
            entry["source_path"] = str(Path(raw_dir) / entry["assets"][0])
            entries.append(entry)
    return entries


def _parse_xiaohongshu_entries(raw_dir: Path):
    """Scan either ``<note_id>/<note_id>.md`` (current spec) or the older flat
    layout ``<note_id>.md`` plus companion ``<note_id>.mp4`` files.

    Either layout produces one record per note id. Asset files are relative paths
    when nested, or absolute paths when flat (caller records both paths).
    """
    entries = []
    if not raw_dir.exists():
        return entries

    nested: dict[str, Path] = {}
    flat: dict[str, Path] = {}
    for child in sorted(raw_dir.iterdir()):
        if child.is_dir() and PY_ID_RE.match(child.name):
            nested[child.name] = child
        elif child.is_file() and PY_ID_RE.match(child.stem):
            flat.setdefault(child.stem, child.parent)

    for note_id, note_dir in nested.items():
        asset_files = []
        has_md = False
        for grand in note_dir.iterdir():
            if grand.is_file() and grand.suffix.lower() == ".md":
                if grand.stem == note_id:
                    has_md = True
                continue
            if grand.is_file():
                asset_files.append(str(grand.relative_to(note_dir)).replace("\\", "/"))
        if not has_md:
            continue
        entries.append({
            "video_id": note_id,
            "note_id": note_id,
            "source_type": "backfill_xiaohongshu",
            "source_path": str(note_dir),
            "asset_files": sorted(asset_files),
            "asset_count": len(asset_files),
        })

    for note_id, parent in flat.items():
        entries.append({
            "video_id": note_id,
            "note_id": note_id,
            "source_type": "backfill_xiaohongshu_flat",
            "source_path": str(parent / f"{note_id}.md"),
            "asset_files": [],
            "asset_count": 1,
        })

    return entries


def backfill(root: Path, *, write: bool = True) -> dict:
    raw_root = root / "10_raw"
    douyin_entries = _parse_douyin_entries(raw_root / DOuyin_DIR)
    xiaohongshu_entries = []
    for xhs_dir in XHS_DIRS:
        xiaohongshu_entries.extend(_parse_xiaohongshu_entries(raw_root / xhs_dir))
    # Deduplicate entries to the same id: nested layout wins over flat.
    deduped: dict[str, dict] = {}
    for entry in xiaohongshu_entries:
        existing = deduped.get(entry["video_id"])
        if not existing or (entry.get("asset_count", 0) > existing.get("asset_count", 0)):
            deduped[entry["video_id"]] = entry
    xiaohongshu_entries = list(deduped.values())

    summary = {
        "douyin": {"scanned": len(douyin_entries), "written": 0, "skipped": 0},
        "xiaohongshu": {"scanned": len(xiaohongshu_entries), "written": 0, "skipped": 0},
        "manifest_path": str(manifest_path(root)),
        "sqlite_path": str(root / "data" / "subtitle_tasks.db"),
    }

    if not write:
        return summary

    store = SQLiteStore(root / "data" / "subtitle_tasks.db")
    manifest = manifest_path(root)

    for platform, entries, status, source in (
        ("douyin", douyin_entries, "backfilled", "backfill"),
        ("xiaohongshu", xiaohongshu_entries, "backfilled", "backfill"),
    ):
        if not entries:
            continue
        for entry in entries:
            entry["status"] = status
        upsert(manifest, platform, entries)
        for entry in entries:
            mark_completed(
                manifest,
                platform,
                entry["video_id"],
                status=status,
                output_path=entry["source_path"],
            )
            store.upsert_videos(
                [_BackfillVideo(
                    video_id=entry["video_id"],
                    title="",
                    uploader="",
                    source_type=entry["source_type"],
                    source_url=entry["source_path"],
                )],
                platform=platform,
            )
            store.update_job(
                (platform, entry["video_id"]),
                status,
                source=source,
            )
        summary[platform]["written"] = len(entries)

    return summary


class _BackfillVideo:
    def __init__(self, video_id: str, title: str, uploader: str, source_type: str, source_url: str):
        self.video_id = video_id
        self.bvid = video_id
        self.title = title
        self.uploader = uploader
        self.source_type = source_type
        self.source_url = source_url


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="把已下载的抖音/小红书文件回填到项目状态库")
    parser.add_argument("--root", type=Path, default=project_root(),
                        help="项目根目录，默认 runtime_paths.project_root()")
    parser.add_argument("--dry-run", action="store_true",
                        help="只扫描，不写库")
    args = parser.parse_args(argv)

    summary = backfill(args.root, write=not args.dry_run)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())