"""Multi-platform manifest for non-Bilibili sources.

Stored at ``data/platform_videos_manifest.json``. Each platform entry holds the
discovered video/note ids and their lightweight metadata. The Bilibili source
remains in ``data/videos_manifest.json`` for backwards compatibility.
"""
import json
import time
from pathlib import Path


SUPPORTED_PLATFORMS = ("douyin", "xiaohongshu")


def manifest_path(project_root: Path) -> Path:
    return project_root / "data" / "platform_videos_manifest.json"


def load(path: Path) -> dict:
    if not path.exists():
        return {"version": 2, "updated_at": None, "sources": {platform: {} for platform in SUPPORTED_PLATFORMS}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": 2, "updated_at": None, "sources": {platform: {} for platform in SUPPORTED_PLATFORMS}}
    if not isinstance(data, dict):
        return {"version": 2, "updated_at": None, "sources": {platform: {} for platform in SUPPORTED_PLATFORMS}}
    sources = data.setdefault("sources", {})
    for platform in SUPPORTED_PLATFORMS:
        sources.setdefault(platform, {})
    if not isinstance(sources, dict):
        sources = {platform: {} for platform in SUPPORTED_PLATFORMS}
        data["sources"] = sources
    return data


def upsert(path: Path, platform: str, entries) -> dict:
    if platform not in SUPPORTED_PLATFORMS:
        raise ValueError(f"unsupported platform: {platform}")
    data = load(path)
    bucket = data["sources"].setdefault(platform, {})
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    for entry in entries:
        video_id = str(entry.get("video_id") or entry.get("note_id"))
        if not video_id:
            continue
        record = bucket.get(video_id, {})
        record.update({k: v for k, v in entry.items() if v not in (None, "")})
        record["video_id"] = video_id
        record.setdefault("first_seen_at", now)
        record["last_seen_at"] = now
        bucket[video_id] = record
    data["updated_at"] = now
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)
    return data


def mark_completed(path: Path, platform: str, video_id: str, *, status: str, output_path: str) -> dict:
    """Update a single entry's status / output / completed_at fields."""
    if platform not in SUPPORTED_PLATFORMS:
        raise ValueError(f"unsupported platform: {platform}")
    data = load(path)
    bucket = data["sources"].setdefault(platform, {})
    record = bucket.get(video_id, {"video_id": video_id})
    record["status"] = status
    record["output_path"] = output_path
    record["completed_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    bucket[video_id] = record
    data["updated_at"] = record["completed_at"]
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)
    return data


def get_entry(path: Path, platform: str, video_id: str):
    data = load(path)
    return data["sources"].get(platform, {}).get(video_id)