import json
import sqlite3
from contextlib import closing
from pathlib import Path


PLATFORM_BILIBILI = "bilibili"


SCHEMA_V2 = """
CREATE TABLE IF NOT EXISTS videos (
    platform TEXT NOT NULL,
    video_id TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    uploader TEXT NOT NULL DEFAULT '',
    source_type TEXT NOT NULL DEFAULT '',
    source_url TEXT NOT NULL DEFAULT '',
    first_seen_at TEXT NOT NULL DEFAULT '',
    last_seen_at TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (platform, video_id)
);
CREATE TABLE IF NOT EXISTS jobs (
    platform TEXT NOT NULL,
    video_id TEXT NOT NULL,
    status TEXT NOT NULL,
    error TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (platform, video_id)
);
CREATE TABLE IF NOT EXISTS subtitle_files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform TEXT NOT NULL DEFAULT 'bilibili',
    video_id TEXT NOT NULL,
    path TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_subtitle_files_video
    ON subtitle_files (platform, video_id);
"""


def _table_columns(db: sqlite3.Connection, table: str) -> list[str]:
    try:
        rows = db.execute(f"PRAGMA table_info({table})").fetchall()
    except sqlite3.OperationalError:
        return []
    return [row[1] for row in rows]


def _migrate_to_v2(db: sqlite3.Connection) -> None:
    """Upgrade pre-platform schema to (platform, video_id) primary keys in place.

    Old layout: videos(bvid PK), jobs(bvid PK), subtitle_files(bvid NOT NULL).
    The previous bvid column is migrated to video_id and platform=bilibili.
    """
    video_columns = _table_columns(db, "videos")
    if not video_columns:
        return
    if "platform" in video_columns and "video_id" in video_columns:
        return
    if "bvid" not in video_columns:
        return

    db.execute("ALTER TABLE videos RENAME TO videos_v1_legacy")
    db.execute("ALTER TABLE jobs RENAME TO jobs_v1_legacy")
    db.execute("ALTER TABLE subtitle_files RENAME TO subtitle_files_v1_legacy")

    db.executescript(SCHEMA_V2)

    db.execute(
        """INSERT INTO videos(platform, video_id, title, uploader, source_type, source_url, first_seen_at, last_seen_at)
           SELECT 'bilibili', bvid, title, uploader, source_type, source_url, '', ''
             FROM videos_v1_legacy"""
    )
    db.execute(
        """INSERT INTO jobs(platform, video_id, status, error, source, updated_at)
           SELECT 'bilibili', bvid, status, error, source, '' FROM jobs_v1_legacy"""
    )
    db.execute(
        """INSERT INTO subtitle_files(id, platform, video_id, path, source, model, created_at)
           SELECT id, 'bilibili', bvid, path, source, model, created_at
             FROM subtitle_files_v1_legacy"""
    )

    db.execute("DROP TABLE videos_v1_legacy")
    db.execute("DROP TABLE jobs_v1_legacy")
    db.execute("DROP TABLE subtitle_files_v1_legacy")


class SQLiteStore:
    """Multi-platform SQLite store. The default platform is 'bilibili'."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as db:
            _migrate_to_v2(db)
            db.executescript(SCHEMA_V2)
            db.commit()

    def connect(self):
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        return db

    # -------- queries --------

    def jobs(self, platform: str = PLATFORM_BILIBILI):
        with closing(self.connect()) as db:
            return {
                row["video_id"]: dict(row)
                for row in db.execute(
                    "SELECT * FROM jobs WHERE platform = ?", (platform,)
                )
            }

    def all_jobs(self):
        with closing(self.connect()) as db:
            return {
                (row["platform"], row["video_id"]): dict(row)
                for row in db.execute("SELECT * FROM jobs")
            }

    def get(self, platform: str, video_id: str):
        with closing(self.connect()) as db:
            row = db.execute(
                "SELECT * FROM videos WHERE platform = ? AND video_id = ?",
                (platform, video_id),
            ).fetchone()
            return dict(row) if row else None

    # -------- writes --------

    def upsert_videos(self, videos, *, platform: str = PLATFORM_BILIBILI):
        with closing(self.connect()) as db:
            for video in videos:
                video_id = getattr(video, "video_id", None) or video.bvid
                db.execute(
                    """INSERT INTO videos(platform,video_id,title,uploader,source_type,source_url,first_seen_at,last_seen_at)
                       VALUES(?,?,?,?,?,?,'','')
                       ON CONFLICT(platform,video_id) DO UPDATE SET
                         title=excluded.title,
                         uploader=excluded.uploader,
                         source_type=excluded.source_type,
                         source_url=excluded.source_url,
                         last_seen_at=CURRENT_TIMESTAMP""",
                    (
                        platform,
                        str(video_id),
                        getattr(video, "title", ""),
                        getattr(video, "uploader", ""),
                        getattr(video, "source_type", ""),
                        getattr(video, "source_url", ""),
                    ),
                )
            db.commit()

    def update_job(self, key, status, error="", source="", *, platform: str = PLATFORM_BILIBILI):
        """Backwards-compatible entry.

    Older callers pass a bvid string as the first parameter; in that case the
    platform defaults to 'bilibili'. New callers should pass a (platform, video_id)
    tuple.
        """
        if isinstance(key, tuple):
            platform, video_id = key
        else:
            video_id = key
        with closing(self.connect()) as db:
            db.execute(
                """INSERT INTO jobs(platform,video_id,status,error,source,updated_at)
                   VALUES(?,?,?,?,?,CURRENT_TIMESTAMP)
                   ON CONFLICT(platform,video_id) DO UPDATE SET
                     status=excluded.status,
                     error=excluded.error,
                     source=excluded.source,
                     updated_at=CURRENT_TIMESTAMP""",
                (platform, str(video_id), status, error, source),
            )
            db.commit()

    # -------- migrations --------

    def migrate_json(self, state_path: Path, legacy_path: Path | None = None):
        if any(self.all_jobs().values()):
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
                if not bvid:
                    continue
                stub = type(
                    "Video",
                    (),
                    {
                        "bvid": bvid,
                        "video_id": bvid,
                        "title": "",
                        "uploader": "",
                        "source_type": "",
                        "source_url": "",
                    },
                )()
                self.upsert_videos([stub])
                self.update_job(bvid, record.get("status", "queued"), record.get("error", ""), record.get("source", ""))
            break

    # -------- helpers --------

    def is_completed(self, platform: str, video_id: str) -> bool:
        with closing(self.connect()) as db:
            row = db.execute(
                "SELECT status FROM jobs WHERE platform = ? AND video_id = ?",
                (platform, video_id),
            ).fetchone()
            return bool(row and row["status"] in {"success", "native_success", "asr_success", "assets_downloaded"})