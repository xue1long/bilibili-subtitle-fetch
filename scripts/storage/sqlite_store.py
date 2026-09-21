import json
import sqlite3
from contextlib import closing
from pathlib import Path


SCHEMA = """
CREATE TABLE IF NOT EXISTS videos (
    bvid TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT '',
    uploader TEXT NOT NULL DEFAULT '',
    source_type TEXT NOT NULL DEFAULT '',
    source_url TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS jobs (
    bvid TEXT PRIMARY KEY REFERENCES videos(bvid),
    status TEXT NOT NULL,
    error TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS subtitle_files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bvid TEXT NOT NULL REFERENCES videos(bvid),
    path TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


class SQLiteStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as db:
            db.executescript(SCHEMA)
            db.commit()

    def connect(self):
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        return db

    def jobs(self):
        with closing(self.connect()) as db:
            return {row["bvid"]: dict(row) for row in db.execute("SELECT * FROM jobs")}

    def upsert_videos(self, videos):
        with closing(self.connect()) as db:
            for video in videos:
                db.execute("""INSERT INTO videos(bvid,title,uploader,source_type,source_url)
                    VALUES(?,?,?,?,?) ON CONFLICT(bvid) DO UPDATE SET title=excluded.title,
                    uploader=excluded.uploader,source_type=excluded.source_type,source_url=excluded.source_url,
                    updated_at=CURRENT_TIMESTAMP""",
                    (video.bvid, video.title, video.uploader, video.source_type, video.source_url))
            db.commit()

    def update_job(self, bvid, status, error="", source=""):
        with closing(self.connect()) as db:
            db.execute("""INSERT INTO jobs(bvid,status,error,source) VALUES(?,?,?,?)
                ON CONFLICT(bvid) DO UPDATE SET status=excluded.status,error=excluded.error,
                source=excluded.source,updated_at=CURRENT_TIMESTAMP""", (bvid, status, error, source))
            db.commit()

    def migrate_json(self, state_path: Path, legacy_path: Path | None = None):
        if any(self.jobs().values()):
            return
        candidates = [state_path]
        if legacy_path:
            candidates.append(legacy_path)
        for candidate in candidates:
            if not candidate.exists():
                continue
            try:
                data = json.loads(candidate.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            records = data.get("jobs", data.get("records", {})) if isinstance(data, dict) else {}
            for bvid, record in records.items():
                self.upsert_videos([type("Video", (), {"bvid": bvid, "title": "", "uploader": "", "source_type": "", "source_url": ""})()])
                self.update_job(bvid, record.get("status", "queued"), record.get("error", ""), record.get("source", ""))
            break
