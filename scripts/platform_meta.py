"""Collect video metadata from local sources for every supported platform.

Each platform keeps its metadata somewhere different, so this module knows one
reader per platform and normalises everything into::

    {video_id: {"title": str, "uploader": str, "published_at": "YYYY-MM-DD",
                "downloaded_at": "YYYY-MM-DD HH:MM:SS", ...}}

Sources
-------
bilibili
    ``<产物根>/01_B站视频转录/*.md`` frontmatter (``video_published_at``,
    ``uploader``, ``title``) plus ``download_list.json`` (``downloaded_at``).
    The raw folder is named in Chinese, so ``os.listdir`` is used instead of
    ``glob`` (glob mis-handles the path on this machine).
douyin
    The "抖珍藏" local library under
    ``D:/admin/下载/Bili/抖音视频/data/.appdata/``. Each ``db_*.js`` file holds
    ``window.<name>_base64="<gzip+base64 JSON>"``.
xiaohongshu
    ``<产物根>/03_小红书/*.json`` — a mix of plain note arrays and
    ``*.state.json`` wrappers holding the notes under a ``notes`` key. They
    carry title, author, the original share URL and engagement counts, but
    **no publish date**.

The browser fallbacks live in ``platform_meta_browser.py``.
"""
from __future__ import annotations

import base64
import gzip
import json
import os
import re
from datetime import datetime
from pathlib import Path

from runtime_paths import project_root, raw_dir

BILIBILI_DIR = "01_B站视频转录"
XHS_DIRS = ("03_小红书", "03_小红书收藏夹")
DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})")
FRONTMATTER_FIELDS = ("title", "uploader", "bvid", "url", "view_count",
                      "like_count", "video_published_at")


def default_appdata() -> Path:
    """Location of the 抖珍藏 local library."""
    return Path(os.environ.get(
        "DOUZHENCANG_APPDATA",
        r"D:\admin\下载\Bili\抖音视频\data\.appdata"))


def parse_frontmatter(text: str) -> dict[str, str]:
    """Read simple ``key: value`` pairs from a YAML frontmatter block."""
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    block = text[3:end] if end != -1 else text
    out: dict[str, str] = {}
    for line in block.splitlines():
        if not line.strip() or line.startswith(" ") or line.startswith("\t"):
            continue
        key, sep, value = line.partition(":")
        if not sep:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        out[key.strip()] = value
    return out


def read_bilibili_frontmatter(directory: Path) -> dict[str, dict]:
    """Collect metadata from every Bilibili transcript that has frontmatter."""
    out: dict[str, dict] = {}
    if not directory.exists():
        return out
    for name in os.listdir(directory):
        if not name.endswith(".md"):
            continue
        try:
            with open(directory / name, encoding="utf-8", errors="replace") as fh:
                head = fh.read(3000)
        except OSError:
            continue
        fields = parse_frontmatter(head)
        if not fields:
            continue
        # Two frontmatter dialects exist: `bvid:` and `bv:`. The file name is
        # the reliable key in both cases.
        bvid = fields.get("bvid") or fields.get("bv") or name[:-3]
        entry = out.setdefault(bvid, {})
        if fields.get("title"):
            entry["title"] = fields["title"]
        if fields.get("uploader"):
            entry["uploader"] = fields["uploader"]
        published = DATE_RE.match(fields.get("video_published_at", ""))
        if published:
            entry["published_at"] = published.group(1)
        if fields.get("view_count"):
            entry["view_count"] = fields["view_count"]
        if fields.get("like_count"):
            entry["like_count"] = fields["like_count"]
    return out


def read_download_list(path: Path) -> dict[str, dict]:
    """Read ``download_list.json`` — it records when each file was fetched."""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    records = data.get("records", {}) if isinstance(data, dict) else {}
    out: dict[str, dict] = {}
    for bvid, record in records.items():
        if not isinstance(record, dict):
            continue
        stamp = record.get("downloaded_at")
        if not stamp:
            continue
        out[bvid] = {"downloaded_at": stamp[:19].replace("T", " ")}
    return out


def decode_appdata_file(path: Path) -> dict:
    """Decode a 抖珍藏 ``db_*.js`` file: ``window.x_base64="<gzip+base64>"``."""
    text = path.read_text(encoding="utf-8")
    match = re.search(r'base64="([^"]+)"', text)
    if not match:
        raise ValueError(f"no base64 payload in {path}")
    raw = gzip.decompress(base64.b64decode(match.group(1)))
    return json.loads(raw.decode("utf-8"))


def read_douzh_videos(appdata: Path) -> dict[str, dict]:
    """Merge the 抖珍藏 video/author/text tables into one mapping."""
    videos = decode_appdata_file(appdata / "db_videos.js")
    authors = decode_appdata_file(appdata / "db_authors.js")
    texts = decode_appdata_file(appdata / "db_texts.js")
    out: dict[str, dict] = {}
    for video_id, entry in videos.items():
        record = out.setdefault(video_id, {})
        caption = texts.get(video_id, "") or ""
        if caption:
            record["title"] = caption.split("\n")[0].strip()
        if entry.get("createTime"):
            try:
                record["published_at"] = datetime.fromtimestamp(
                    entry["createTime"]).strftime("%Y-%m-%d")
            except (TypeError, ValueError, OSError):
                pass
        if entry.get("diggCount") is not None:
            record["like_count"] = entry["diggCount"]
        author = authors.get(str(entry.get("authorId"))) or {}
        names = [n for n in author.get("nicknames", []) if n]
        if names:
            record["uploader"] = names[-1]
    return out


def read_douyin(appdata: Path) -> dict[str, dict]:
    """Read the 抖珍藏 local library into a metadata mapping."""
    try:
        return read_douzh_videos(appdata)
    except (OSError, ValueError, json.JSONDecodeError):
        return {}


def merge(*sources: dict[str, dict]) -> dict[str, dict]:
    """Merge mappings; later sources win for non-empty values."""
    out: dict[str, dict] = {}
    for source in sources:
        for video_id, record in source.items():
            entry = out.setdefault(video_id, {})
            for key, value in record.items():
                if value not in (None, ""):
                    entry[key] = value
    return out


def read_xiaohongshu_json(directory: Path) -> dict[str, dict]:
    """Read the Xiaohongshu note lists saved under the collection folder.

    The files come in two shapes: plain arrays of note objects, and
    ``*.state.json`` wrappers that hold the notes under a ``notes`` key. Both
    carry ``noteId``/``title``/``author``/``url`` plus engagement counts.
    """
    out: dict[str, dict] = {}
    if not directory.exists():
        return out
    for name in os.listdir(directory):
        if not name.endswith(".json"):
            continue
        try:
            with open(directory / name, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict):
            candidates = data.get("notes") or []
        elif isinstance(data, list):
            candidates = data
        else:
            continue
        for note in candidates:
            if not isinstance(note, dict):
                continue
            note_id = note.get("noteId") or note.get("note_id")
            if not note_id:
                continue
            entry = out.setdefault(str(note_id), {})
            if note.get("title"):
                entry["title"] = note["title"]
            if note.get("author"):
                entry["uploader"] = note["author"]
            if note.get("url"):
                entry["source_url"] = note["url"]
            if note.get("type"):
                entry["note_type"] = note["type"]
            for source_key, target_key in (("likedCount", "like_count"),
                                           ("collectedCount", "collected_count"),
                                           ("commentCount", "comment_count"),
                                           ("shareCount", "share_count")):
                if note.get(source_key) not in (None, ""):
                    entry[target_key] = note[source_key]
    return out


def read_videos_manifest(path: Path) -> dict[str, dict]:
    """Read the Bilibili source manifest — it holds titles for 660 videos."""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    out: dict[str, dict] = {}
    for bvid, record in (data.get("videos") or {}).items():
        if not isinstance(record, dict):
            continue
        entry: dict[str, str] = {}
        if record.get("title"):
            entry["title"] = record["title"]
        if record.get("uploader"):
            entry["uploader"] = record["uploader"]
        out[bvid] = entry
    return out


def collect(root: Path | None = None, raw: Path | None = None) -> dict[str, dict[str, dict]]:
    """Gather every local source, keyed by platform then video id.

    ``root`` is the project root, ``raw`` the artefact root. When ``root`` is
    given explicitly the artefact root follows it (``root/10_raw``) so tests
    can work inside a temporary directory; otherwise the artefact root comes
    from ``runtime_paths.raw_dir()`` — it may live outside the repository
    (``BILIBILI_SFETCH_RAW_DIR``).
    """
    explicit_root = root is not None
    root = root or project_root()
    if raw is None:
        raw = root / "10_raw" if explicit_root else raw_dir()
    bilibili_dir = raw / BILIBILI_DIR
    bili = merge(
        read_bilibili_frontmatter(bilibili_dir),
        read_videos_manifest(root / "data" / "videos_manifest.json"),
    )
    for bvid, record in read_download_list(
            bilibili_dir / "download_list.json").items():
        bili.setdefault(bvid, {}).update(record)
    xiaohongshu: dict[str, dict] = {}
    for directory in XHS_DIRS:
        xiaohongshu.update(read_xiaohongshu_json(raw / directory))
    return {
        "bilibili": bili,
        "douyin": read_douyin(default_appdata()),
        "xiaohongshu": xiaohongshu,
    }


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                    encoding="utf-8")
