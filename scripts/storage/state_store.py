import json
import time
from pathlib import Path


def state_path(project_root: Path) -> Path:
    return project_root / "data" / "subtitle_jobs.json"


def load(path: Path, legacy_path: Path | None = None) -> dict:
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("jobs"), dict):
                return data
        except (OSError, json.JSONDecodeError):
            pass
    jobs = {}
    if legacy_path and legacy_path.exists():
        try:
            legacy = json.loads(legacy_path.read_text(encoding="utf-8"))
            jobs = legacy.get("records", {}) if isinstance(legacy, dict) else {}
        except (OSError, json.JSONDecodeError):
            pass
    return {"version": 1, "updated_at": None, "jobs": jobs}


def update(path: Path, bvid: str, status: str, *, error: str = "", source: str = "") -> dict:
    data = load(path)
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    record = dict(data["jobs"].get(bvid, {}))
    record.update({"bvid": bvid, "status": status, "updated_at": now})
    if error:
        record["error"] = error
    if source:
        record["source"] = source
    data["jobs"][bvid] = record
    data["updated_at"] = now
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)
    return data
