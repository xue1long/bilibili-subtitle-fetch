import json
import time
from pathlib import Path


def manifest_path(project_root: Path) -> Path:
    return project_root / "data" / "videos_manifest.json"


def load(path: Path) -> dict:
    if not path.exists():
        return {"version": 1, "updated_at": None, "videos": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("videos"), dict):
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return {"version": 1, "updated_at": None, "videos": {}}


def upsert(path: Path, videos) -> dict:
    data = load(path)
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    for video in videos:
        data["videos"][video.bvid] = {
            "bvid": video.bvid,
            "title": video.title,
            "uploader": video.uploader,
            "source_type": video.source_type,
            "source_id": video.source_id,
            "source_url": video.source_url,
            "first_seen_at": data["videos"].get(video.bvid, {}).get("first_seen_at", now),
            "last_seen_at": now,
        }
    data["updated_at"] = now
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)
    return data
