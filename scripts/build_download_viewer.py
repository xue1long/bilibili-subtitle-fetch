"""生成「下载列表」查看器（单文件 HTML，零依赖）。

合并四路数据源，产出统一条目列表：

1. ``data/subtitle_tasks.db``      —— 任务状态主库（权威来源）
2. ``data/videos_manifest.json``   —— 来源发现清单
3. ``产物根/**/download_list.json``—— 旧入口 / 批处理 / 救援的兼容记录
4. ``产物根/``                     —— 磁盘上的实际产物（字幕、媒体、图文）

用法::

    python scripts/build_download_viewer.py
    python scripts/build_download_viewer.py -o docs/download_viewer.html
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import time
from pathlib import Path
from html import escape as html_escape
from urllib.parse import quote

from runtime_paths import raw_dir

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = ROOT / "data" / "download_viewer.html"

BV_RE = re.compile(r"^BV[0-9A-Za-z]{10}$")

# 归一化状态：数据库 / 旧 JSON 里的原始值 -> (展示态, 中文标签)
STATUS_MAP = {
    "native_success": ("success", "已抓取"),
    "success": ("success", "已抓取"),
    "asr_success": ("success", "ASR 转录"),
    "native_running": ("running", "进行中"),
    "running": ("running", "进行中"),
    "asr_running": ("running", "进行中"),
    "failed": ("failed", "失败"),
    "no_subtitle": ("no_subtitle", "无字幕"),
    "paused": ("paused", "已暂停"),
    "queued": ("queued", "排队中"),
    "asr_queued": ("queued", "ASR 排队"),
    "discovered": ("discovered", "已发现"),
    "": ("discovered", "已发现"),
}

ERROR_HINTS = {
    "SUBTITLE_NOT_OBSERVED": "页面未观察到字幕请求：可能没有 AI 字幕，或页面尚未加载完成",
    "NO_SUBTITLE": "该视频没有可用字幕，需要 ASR 兜底",
    "SUBTITLE_API_FAILED": "字幕接口调用失败",
    "LOGIN_REQUIRED": "需要登录态，已过期",
    "RATE_LIMITED": "触发平台风控限流",
    "BROWSER_LAUNCH_FAILED": "浏览器启动失败",
}

MEDIA_SUFFIXES = {".md", ".srt", ".vtt", ".mp4", ".webp", ".jpg", ".jpeg", ".png", ".mp3", ".wav", ".m4a"}


# --------------------------------------------------------------------------- #
# 最小 frontmatter 解析（无 pyyaml 依赖）
# --------------------------------------------------------------------------- #
def parse_frontmatter(text: str) -> dict:
    """解析 ``---`` 包裹的 YAML frontmatter，只取顶层标量与 ``|`` 块。"""
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    block = text[3:end]
    data: dict = {}
    key = None
    buf: list[str] = []
    in_block = False
    for line in block.splitlines():
        if in_block:
            if line.startswith((" ", "\t")):
                buf.append(line.strip())
                continue
            data[key] = "\n".join(buf).strip()
            key, buf, in_block = None, [], False
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            continue
        name, _, value = line.partition(":")
        name = name.strip()
        value = value.strip()
        if value in ("|", ">", "|-", ">-"):
            key, buf, in_block = name, [], True
            continue
        if value[:1] in ('"', "'") and value[-1:] == value[:1] and len(value) > 1:
            value = value[1:-1]
        data[name] = value
    if in_block and key:
        data[key] = "\n".join(buf).strip()
    return data


# --------------------------------------------------------------------------- #
# 数据源读取
# --------------------------------------------------------------------------- #
def read_sqlite(db_path: Path) -> tuple[dict, dict]:
    if not db_path.exists():
        return {}, {}
    uri = f"file:{db_path.as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as db:
        db.row_factory = sqlite3.Row
        videos = {r["bvid"]: dict(r) for r in db.execute("SELECT * FROM videos")}
        jobs = {r["bvid"]: dict(r) for r in db.execute("SELECT * FROM jobs")}
    return videos, jobs


def read_manifest(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data.get("videos", {}) if isinstance(data, dict) else {}


def read_legacy_lists(raw_root: Path) -> dict:
    """合并所有 ``download_list.json``，后写的同名 BV 覆盖先写的。"""
    merged: dict = {}
    for path in sorted(raw_root.rglob("download_list.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        records = data.get("records", {}) if isinstance(data, dict) else {}
        for bvid, record in records.items():
            merged[bvid] = {**record, "_source_file": str(path)}
    return merged


def scan_raw(raw_root: Path) -> tuple[dict, dict, list]:
    """扫描磁盘产物。

    归属规则：文件名以条目 ID 开头（``<id>.md`` / ``<id>_xxx.mp4``）算该条目的产物；
    小红书图文笔记的图片放在以 note_id 命名的子目录里，也归属该条目。

    返回 ``(bilibili_by_bv, xiaohongshu_by_note, 所有媒体文件)``。
    """
    bili: dict = {}
    xhs: dict = {}
    if not raw_root.exists():
        return bili, xhs, []

    # --- 先确定每个条目的元数据与主 md ---
    for md in sorted(raw_root.rglob("*.md")):
        text = md.read_text(encoding="utf-8", errors="replace")
        meta = parse_frontmatter(text)
        if BV_RE.match(md.stem):
            bili[md.stem] = {"dir": md.parent, "meta": {}, "files": [md]}
            continue
        note_id = meta.get("note_id") or md.stem
        xhs[note_id] = {"dir": md.parent, "meta": meta, "files": [md]}

    # --- 再把媒体文件分配给所属条目 ---
    groups = {**bili, **xhs}
    for f in sorted(raw_root.rglob("*")):
        if not f.is_file() or f.suffix.lower() not in MEDIA_SUFFIXES:
            continue
        if f.suffix.lower() == ".md":
            continue
        owner = None
        for key, entry in groups.items():
            if f.stem == key or f.stem.startswith(key + "_"):
                owner = entry
                break
            # 小红书图文：图片在以 note_id 命名的子目录里（<dir>/<note_id>/*.webp）
            if f.parent.name == key and (f.parent == entry["dir"] or f.parent.parent == entry["dir"]):
                owner = entry
                break
        if owner is not None and f not in owner["files"]:
            owner["files"].append(f)

    all_media = [f for f in sorted(raw_root.rglob("*"))
                 if f.is_file() and f.suffix.lower() in MEDIA_SUFFIXES and not f.name.startswith(".")]
    return bili, xhs, all_media


# --------------------------------------------------------------------------- #
# 合并
# --------------------------------------------------------------------------- #
def fmt_duration(seconds) -> float | None:
    try:
        value = float(seconds)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def rel_link(out_path: Path, target: Path) -> str:
    try:
        rel = os.path.relpath(target, out_path.parent)
    except ValueError:
        rel = str(target)
    return quote(Path(rel).as_posix(), safe="/._-")


def build_items(root: Path, out_path: Path) -> tuple[list[dict], dict]:
    db_videos, jobs = read_sqlite(root / "data" / "subtitle_tasks.db")
    manifest = read_manifest(root / "data" / "videos_manifest.json")
    legacy = read_legacy_lists(raw_dir())
    bili_disk, xhs_disk, _ = scan_raw(raw_dir())

    items: list[dict] = []

    # ---- B 站条目 ----
    for bvid in sorted(set(db_videos) | set(jobs) | set(manifest) | set(legacy) | set(bili_disk)):
        job = jobs.get(bvid, {})
        man = manifest.get(bvid, {})
        leg = legacy.get(bvid, {})
        disk = bili_disk.get(bvid, {})

        raw_status = job.get("status") or leg.get("status") or ""
        state, label = STATUS_MAP.get(raw_status, (raw_status or "discovered", raw_status or "已发现"))

        error = job.get("error") or leg.get("error") or ""
        error_code = leg.get("error_code") or ""

        files = []
        for f in disk.get("files", []):
            try:
                size = f.stat().st_size
            except OSError:
                size = 0
            files.append({
                "name": f.name,
                "rel": rel_link(out_path, f),
                "abs": str(f),
                "size": size,
                "ext": f.suffix.lower(),
            })

        items.append({
            "id": bvid,
            "platform": "bilibili",
            "title": man.get("title") or db_videos.get(bvid, {}).get("title") or "",
            "uploader": man.get("uploader") or db_videos.get(bvid, {}).get("uploader") or "",
            "state": state,
            "stateLabel": label,
            "rawStatus": raw_status,
            "error": error,
            "errorCode": error_code,
            "errorHint": ERROR_HINTS.get(error_code, ""),
            "duration": fmt_duration(leg.get("duration_sec")),
            "source": job.get("source") or "native",
            "sourceType": man.get("source_type") or db_videos.get(bvid, {}).get("source_type") or "",
            "sourceUrl": man.get("source_url") or db_videos.get(bvid, {}).get("source_url") or "",
            "firstSeen": man.get("first_seen_at", ""),
            "lastSeen": man.get("last_seen_at", ""),
            "updatedAt": job.get("updated_at", "") or leg.get("downloaded_at", ""),
            "downloadedAt": leg.get("downloaded_at", ""),
            "url": f"https://www.bilibili.com/video/{bvid}",
            "files": files,
            "hasDisk": bool(files),
            "hasLegacy": bvid in legacy,
            "probe": leg.get("probe", {}),
        })

    # ---- 小红书条目 ----
    for note_id, disk in sorted(xhs_disk.items()):
        meta = disk.get("meta", {})
        files = []
        for f in disk.get("files", []):
            try:
                size = f.stat().st_size
            except OSError:
                size = 0
            files.append({
                "name": f.name,
                "rel": rel_link(out_path, f),
                "abs": str(f),
                "size": size,
                "ext": f.suffix.lower(),
            })
        dur = meta.get("video_duration_seconds")
        items.append({
            "id": note_id,
            "platform": "xiaohongshu",
            "title": meta.get("title", ""),
            "uploader": meta.get("uploader", ""),
            "state": "success",
            "stateLabel": "已下载",
            "rawStatus": "downloaded",
            "error": "",
            "errorCode": "",
            "errorHint": "",
            "duration": fmt_duration(dur),
            "source": meta.get("platform", "xiaohongshu"),
            "sourceType": "note",
            "sourceUrl": meta.get("url", ""),
            "firstSeen": "",
            "lastSeen": "",
            "updatedAt": "",
            "downloadedAt": "",
            "url": meta.get("url", ""),
            "files": files,
            "hasDisk": True,
            "hasLegacy": False,
            "probe": {},
            "description": meta.get("description", ""),
        })

    meta = {
        "generatedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
        "dbJobs": len(jobs),
        "dbVideos": len(db_videos),
        "manifest": len(manifest),
        "legacy": len(legacy),
        "diskFiles": sum(len(i["files"]) for i in items),
    }
    return items, meta


# --------------------------------------------------------------------------- #
# 渲染
# --------------------------------------------------------------------------- #
HTML_TEMPLATE = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>下载列表 · bilibili-subtitle-fetch</title>
<style>
:root{
  --bg:#f5f6f8; --surface:#fff; --surface-2:#fafbfc; --line:#e4e7ec; --line-2:#eef0f3;
  --text:#16191d; --muted:#667085; --faint:#98a2b3;
  --accent:#2563eb; --accent-bg:#eff4ff; --accent-line:#c7d7fe;
  --ok:#127a3d; --ok-bg:#edfaf1; --ok-line:#b6e4c6;
  --err:#b42318; --err-bg:#fef3f2; --err-line:#f3c6c1;
  --warn:#a15c07; --warn-bg:#fffaeb; --warn-line:#f5dda6;
  --idle:#475467; --idle-bg:#f2f4f7; --idle-line:#dce0e6;
  --r:10px; --sh:0 1px 2px rgba(16,24,40,.05),0 1px 3px rgba(16,24,40,.07);
}
*{box-sizing:border-box}
html,body{margin:0;padding:0}
body{
  background:var(--bg); color:var(--text);
  font:14px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif;
  -webkit-font-smoothing:antialiased;
}
.wrap{max-width:1440px;margin:0 auto;padding:24px 24px 64px}

/* ---- header ---- */
header{display:flex;flex-wrap:wrap;align-items:flex-end;gap:16px;margin-bottom:20px}
h1{font-size:20px;font-weight:650;margin:0;letter-spacing:-.01em}
h1 .sub{font-size:12px;font-weight:500;color:var(--faint);margin-left:8px;letter-spacing:0}
.srcline{font-size:12px;color:var(--muted);margin-top:4px}
.srcline code{background:var(--surface);border:1px solid var(--line);border-radius:5px;padding:1px 5px;font-size:11px;color:#344054}

/* ---- stat tiles ---- */
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px;margin-bottom:16px}
.tile{background:var(--surface);border:1px solid var(--line);border-radius:var(--r);padding:12px 14px;box-shadow:var(--sh)}
.tile .k{font-size:11px;color:var(--muted);letter-spacing:.02em;display:flex;align-items:center;gap:6px}
.tile .v{font-size:24px;font-weight:640;margin-top:2px;line-height:1.15;font-variant-numeric:tabular-nums}
.tile .v small{font-size:12px;font-weight:500;color:var(--faint);margin-left:3px}
.dot{width:7px;height:7px;border-radius:50%;display:inline-block}
.dot.success{background:#12b76a}.dot.failed{background:#f04438}.dot.running{background:#f79009}
.dot.discovered{background:#98a2b3}.dot.no_subtitle{background:#7a5af8}.dot.paused{background:#667085}

/* ---- toolbar ---- */
.bar{display:flex;flex-wrap:wrap;gap:8px;align-items:center;background:var(--surface);
  border:1px solid var(--line);border-radius:var(--r);padding:10px 12px;box-shadow:var(--sh);margin-bottom:14px}
input[type=search],select{
  font:inherit;color:var(--text);background:var(--surface-2);border:1px solid var(--line);
  border-radius:7px;padding:6px 9px;height:32px;outline:none;
}
input[type=search]{min-width:240px;flex:1;max-width:380px}
input[type=search]:focus,select:focus{border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-bg)}
label.chk{display:inline-flex;align-items:center;gap:6px;font-size:13px;color:var(--muted);cursor:pointer;user-select:none}
.spacer{flex:1}
.count{font-size:12px;color:var(--muted);font-variant-numeric:tabular-nums}
.btn{font:inherit;font-size:13px;background:var(--surface);border:1px solid var(--line);border-radius:7px;
  padding:6px 11px;cursor:pointer;color:#344054;height:32px}
.btn:hover{background:var(--surface-2);border-color:#d0d5dd}

/* ---- table ---- */
.card{background:var(--surface);border:1px solid var(--line);border-radius:var(--r);box-shadow:var(--sh);overflow:hidden}
table{width:100%;border-collapse:collapse}
thead th{
  position:sticky;top:0;z-index:2;background:var(--surface-2);text-align:left;
  font-size:11px;font-weight:600;color:var(--muted);letter-spacing:.03em;
  padding:9px 12px;border-bottom:1px solid var(--line);white-space:nowrap;
}
tbody td{padding:9px 12px;border-bottom:1px solid var(--line-2);vertical-align:top}
tbody tr{cursor:pointer}
tbody tr:hover{background:var(--accent-bg)}
tbody tr.sel{background:var(--accent-bg);box-shadow:inset 3px 0 0 var(--accent)}
.cell-title{max-width:520px}
.cell-title .t{font-weight:520;line-height:1.45;display:block}
.cell-title .id{font-size:11px;color:var(--faint);font-family:ui-monospace,SFMono-Regular,Menlo,monospace;margin-top:2px}
.cell-err{max-width:260px;font-size:12px;color:var(--err);line-height:1.45}
.badge{display:inline-block;padding:2px 8px;border-radius:20px;font-size:11px;font-weight:600;white-space:nowrap;border:1px solid}
.badge.success{background:var(--ok-bg);color:var(--ok);border-color:var(--ok-line)}
.badge.failed{background:var(--err-bg);color:var(--err);border-color:var(--err-line)}
.badge.running{background:var(--warn-bg);color:var(--warn);border-color:var(--warn-line)}
.badge.discovered{background:var(--idle-bg);color:var(--idle);border-color:var(--idle-line)}
.badge.no_subtitle{background:#f4f3ff;color:#5925dc;border-color:#d9d6fe}
.badge.paused{background:var(--idle-bg);color:var(--idle);border-color:var(--idle-line)}
.badge.queued{background:var(--accent-bg);color:var(--accent);border-color:var(--accent-line)}
.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px;color:var(--muted)}
.tag{display:inline-block;padding:1px 6px;border-radius:4px;background:var(--surface-2);border:1px solid var(--line);
  color:var(--muted);font-size:11px;margin-right:4px}
.files-cell{font-size:12px;color:var(--muted);max-width:220px}
.files-cell .f{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.empty{padding:48px;text-align:center;color:var(--faint)}

/* ---- drawer ---- */
.mask{position:fixed;inset:0;background:rgba(16,24,40,.32);opacity:0;pointer-events:none;transition:opacity .18s}
.mask.on{opacity:1;pointer-events:auto}
.drawer{position:fixed;top:0;right:0;height:100%;width:min(520px,94vw);background:var(--surface);
  box-shadow:-8px 0 24px rgba(16,24,40,.12);transform:translateX(100%);transition:transform .2s ease;
  display:flex;flex-direction:column;z-index:20}
.drawer.on{transform:none}
.dhead{padding:16px 18px;border-bottom:1px solid var(--line);display:flex;align-items:flex-start;gap:12px}
.dhead h2{font-size:15px;font-weight:620;margin:0 0 4px;line-height:1.4}
.dbody{overflow-y:auto;padding:16px 18px 40px;flex:1}
.close{margin-left:auto;background:none;border:none;font-size:20px;line-height:1;color:var(--faint);cursor:pointer;padding:0 2px}
.close:hover{color:var(--text)}
dl{display:grid;grid-template-columns:96px 1fr;gap:7px 12px;margin:0 0 18px;font-size:13px}
dt{color:var(--muted);font-size:12px}
dd{margin:0;word-break:break-word}
.sec{font-size:11px;font-weight:650;color:var(--muted);letter-spacing:.05em;margin:20px 0 9px;
  padding-bottom:5px;border-bottom:1px solid var(--line-2)}
.errbox{background:var(--err-bg);border:1px solid var(--err-line);border-radius:8px;padding:10px 12px;font-size:13px;color:var(--err)}
.errbox .code{font-family:ui-monospace,Menlo,monospace;font-size:11px;display:block;margin-top:5px;opacity:.85}
.filelist{list-style:none;padding:0;margin:0}
.filelist li{padding:7px 0;border-bottom:1px solid var(--line-2);font-size:12px;display:flex;gap:10px;align-items:center}
.filelist a{color:var(--accent);text-decoration:none;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.filelist a:hover{text-decoration:underline}
.filelist .sz{margin-left:auto;color:var(--faint);font-variant-numeric:tabular-nums;white-space:nowrap}
.probe{font-family:ui-monospace,Menlo,monospace;font-size:11px;background:var(--surface-2);
  border:1px solid var(--line);border-radius:8px;padding:10px 12px;white-space:pre-wrap;color:#475467}
.warnbox{background:var(--warn-bg);border:1px solid var(--warn-line);border-radius:8px;padding:10px 12px;font-size:13px;color:var(--warn)}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div>
      <h1>下载列表<span class="sub">bilibili-subtitle-fetch</span></h1>
      <div class="srcline">
        数据源 <code>subtitle_tasks.db</code> <code>videos_manifest.json</code>
        <code>download_list.json</code> <code>产物根/</code> &nbsp;·&nbsp; 快照 <span id="genAt"></span>
      </div>
    </div>
    <div class="spacer"></div>
    <div class="srcline" id="sourceCounts"></div>
  </header>

  <div class="stats" id="stats"></div>

  <div class="bar">
    <input type="search" id="q" placeholder="搜索标题 / BV 号 / UP 主 / 错误码…">
    <select id="fState"><option value="">全部状态</option></select>
    <select id="fPlat"><option value="">全部平台</option></select>
    <select id="sort">
      <option value="updated">按更新时间</option>
      <option value="title">按标题</option>
      <option value="id">按 ID</option>
      <option value="duration">按时长</option>
    </select>
    <label class="chk"><input type="checkbox" id="onlyFiles"> 只看有产物</label>
    <div class="spacer"></div>
    <span class="count" id="count"></span>
    <button class="btn" id="expCsv">导出 CSV</button>
    <button class="btn" id="expJson">导出 JSON</button>
  </div>

  <div class="card">
    <table>
      <thead><tr>
        <th style="width:74px">平台</th>
        <th>标题 / ID</th>
        <th style="width:92px">状态</th>
        <th style="width:150px">错误</th>
        <th style="width:70px">时长</th>
        <th style="width:78px">来源</th>
        <th style="width:210px">产物</th>
        <th style="width:132px">更新时间</th>
      </tr></thead>
      <tbody id="tbody"></tbody>
    </table>
    <div class="empty" id="empty" hidden>没有匹配的记录</div>
  </div>
</div>

<div class="mask" id="mask"></div>
<aside class="drawer" id="drawer" aria-hidden="true">
  <div class="dhead">
    <div style="min-width:0">
      <h2 id="dTitle"></h2>
      <div class="mono" id="dId"></div>
    </div>
    <button class="close" id="dClose" aria-label="关闭">×</button>
  </div>
  <div class="dbody" id="dBody"></div>
</aside>

<script>
const DATA = /*__DATA__*/;
const ITEMS = DATA.items, META = DATA.meta;

const STATE_ORDER = ["success","failed","running","no_subtitle","paused","queued","discovered"];
const STATE_LABEL = {success:"已抓取",failed:"失败",running:"进行中",no_subtitle:"无字幕",
  paused:"已暂停",queued:"排队中",discovered:"已发现"};

const esc = s => String(s??"").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const fmtSize = b => b<1024 ? b+" B" : b<1048576 ? (b/1024).toFixed(1)+" KB" : (b/1048576).toFixed(1)+" MB";
const fmtDur = s => s==null ? "—" : (s<60 ? s.toFixed(1)+"s" : Math.floor(s/60)+"m"+String(Math.round(s%60)).padStart(2,"0")+"s");
const platLabel = p => p==="bilibili" ? "B站" : p==="xiaohongshu" ? "小红书" : p;
const dateOnly = s => s ? String(s).slice(0,16).replace("T"," ") : "—";

/* ---- filter options ---- */
const stateSel = document.getElementById("fState");
STATE_ORDER.forEach(s => {
  const n = ITEMS.filter(i => i.state === s).length;
  if (!n) return;
  const o = document.createElement("option");
  o.value = s; o.textContent = STATE_LABEL[s] + " (" + n + ")";
  stateSel.appendChild(o);
});
const platSel = document.getElementById("fPlat");
[...new Set(ITEMS.map(i => i.platform))].sort().forEach(p => {
  const o = document.createElement("option");
  o.value = p; o.textContent = platLabel(p) + " (" + ITEMS.filter(i => i.platform===p).length + ")";
  platSel.appendChild(o);
});

/* ---- stats ---- */
document.getElementById("genAt").textContent = META.generatedAt;
document.getElementById("sourceCounts").innerHTML =
  `状态库 ${META.dbJobs} 任务 / ${META.dbVideos} 视频 · manifest ${META.manifest} · 旧记录 ${META.legacy} · 产物文件 ${META.diskFiles}`;
const statsEl = document.getElementById("stats");
const statDefs = [
  ["全部条目", ITEMS.length, ""],
  ["已抓取", ITEMS.filter(i=>i.state==="success").length, "success"],
  ["失败", ITEMS.filter(i=>i.state==="failed").length, "failed"],
  ["进行中", ITEMS.filter(i=>i.state==="running").length, "running"],
  ["已发现未处理", ITEMS.filter(i=>i.state==="discovered").length, "discovered"],
  ["有产物文件", ITEMS.filter(i=>i.hasDisk).length, ""]
];
statsEl.innerHTML = statDefs.map(([k,v,d]) =>
  `<div class="tile"><div class="k">${d?`<span class="dot ${d}"></span>`:""}${k}</div><div class="v">${v}</div></div>`).join("");

/* ---- filtering ---- */
const q = document.getElementById("q"), sortSel = document.getElementById("sort"), onlyFiles = document.getElementById("onlyFiles");
let selected = null;

function currentList(){
  const kw = q.value.trim().toLowerCase();
  let list = ITEMS.filter(i => {
    if (stateSel.value && i.state !== stateSel.value) return false;
    if (platSel.value && i.platform !== platSel.value) return false;
    if (onlyFiles.checked && !i.hasDisk) return false;
    if (!kw) return true;
    return (i.title+" "+i.id+" "+i.uploader+" "+i.errorCode+" "+i.error+" "+i.stateLabel).toLowerCase().includes(kw);
  });
  const mode = sortSel.value;
  list.sort((a,b) => {
    if (mode === "title") return (a.title||a.id).localeCompare(b.title||b.id,"zh");
    if (mode === "id") return a.id.localeCompare(b.id);
    if (mode === "duration") return (b.duration||-1) - (a.duration||-1);
    return String(b.updatedAt||"").localeCompare(String(a.updatedAt||""));
  });
  return list;
}

function render(){
  const list = currentList();
  const tb = document.getElementById("tbody");
  document.getElementById("count").textContent = `${list.length} / ${ITEMS.length} 条`;
  document.getElementById("empty").hidden = list.length > 0;
  tb.innerHTML = list.map(i => {
    const err = i.error
      ? `<div class="cell-err">${esc(i.error)}</div>` + (i.errorCode ? `<div class="mono" style="font-size:11px;margin-top:2px">${esc(i.errorCode)}</div>` : "")
      : "";
    const files = i.files.length
      ? i.files.slice(0,3).map(f=>`<span class="f" title="${esc(f.abs)}">${esc(f.name)}</span>`).join("") +
        (i.files.length>3 ? `<span class="f" style="color:var(--faint)">+${i.files.length-3} 个文件</span>` : "")
      : `<span style="color:var(--faint)">—</span>`;
    return `<tr data-id="${esc(i.id)}">
      <td><span class="tag">${platLabel(i.platform)}</span></td>
      <td class="cell-title"><span class="t">${esc(i.title || "（无标题）")}</span><span class="id">${esc(i.id)}</span></td>
      <td><span class="badge ${esc(i.state)}">${esc(i.stateLabel)}</span></td>
      <td>${err}</td>
      <td class="mono">${fmtDur(i.duration)}</td>
      <td class="mono">${esc(i.source||"—")}</td>
      <td class="files-cell">${files}</td>
      <td class="mono">${dateOnly(i.updatedAt)}</td>
    </tr>`;
  }).join("");
}

document.getElementById("tbody").addEventListener("click", e => {
  const tr = e.target.closest("tr"); if (!tr) return;
  openDrawer(tr.dataset.id);
});

/* ---- drawer ---- */
const drawer = document.getElementById("drawer"), mask = document.getElementById("mask");
function openDrawer(id){
  const i = ITEMS.find(x => x.id === id); if (!i) return;
  selected = id;
  document.getElementById("dTitle").textContent = i.title || "（无标题）";
  document.getElementById("dId").textContent = i.id;
  const rows = [
    ["状态", `<span class="badge ${i.state}">${esc(i.stateLabel)}</span> <span class="mono">${esc(i.rawStatus||"—")}</span>`],
    ["平台", platLabel(i.platform)],
    ["UP 主", esc(i.uploader || "—")],
    ["时长", fmtDur(i.duration)],
    ["来源", esc(i.source || "—") + (i.sourceType ? ` <span class="tag">${esc(i.sourceType)}</span>` : "")],
    ["首次发现", dateOnly(i.firstSeen)],
    ["最近发现", dateOnly(i.lastSeen)],
    ["更新时间", dateOnly(i.updatedAt)],
    ["磁盘产物", i.hasDisk ? `<span class="badge success">${i.files.length} 个文件</span>` : `<span class="badge discovered">缺失</span>`],
  ];
  let html = `<dl>${rows.map(([k,v])=>`<dt>${k}</dt><dd>${v}</dd>`).join("")}</dl>`;
  if (i.url) html += `<div class="sec">原始链接</div><div><a class="mono" href="${esc(i.url)}" target="_blank" rel="noreferrer">${esc(i.url)}</a></div>`;
  if (i.error){
    html += `<div class="sec">错误</div><div class="errbox">${esc(i.error)}${i.errorCode?`<span class="code">${esc(i.errorCode)}</span>`:""}</div>`;
    if (i.errorHint) html += `<div style="margin-top:8px" class="warnbox">${esc(i.errorHint)}</div>`;
  }
  if (i.files.length){
    html += `<div class="sec">产物文件</div><ul class="filelist">` + i.files.map(f =>
      `<li><a href="${esc(f.rel)}" target="_blank" title="${esc(f.abs)}">${esc(f.name)}</a><span class="sz">${fmtSize(f.size)}</span></li>`).join("") + `</ul>`;
  }
  if (i.description) html += `<div class="sec">简介</div><div style="font-size:13px;white-space:pre-wrap">${esc(i.description)}</div>`;
  if (i.probe && Object.keys(i.probe).length) html += `<div class="sec">Probe</div><div class="probe">${esc(JSON.stringify(i.probe,null,2))}</div>`;
  document.getElementById("dBody").innerHTML = html;
  drawer.classList.add("on"); mask.classList.add("on"); drawer.setAttribute("aria-hidden","false");
}
function closeDrawer(){
  drawer.classList.remove("on"); mask.classList.remove("on"); drawer.setAttribute("aria-hidden","true");
  selected = null;
}
document.getElementById("dClose").onclick = closeDrawer;
mask.onclick = closeDrawer;
document.addEventListener("keydown", e => { if (e.key === "Escape") closeDrawer(); });

/* ---- export ---- */
function download(name, text, type){
  const blob = new Blob([text], {type});
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob); a.download = name; a.click();
  setTimeout(()=>URL.revokeObjectURL(a.href), 1000);
}
document.getElementById("expJson").onclick = () =>
  download("download_list.json", JSON.stringify(currentList(), null, 2), "application/json");
document.getElementById("expCsv").onclick = () => {
  const cols = ["id","platform","title","uploader","state","stateLabel","rawStatus","error","errorCode",
                "duration","source","sourceType","firstSeen","lastSeen","updatedAt","url","files"];
  const q2 = s => '"' + String(s??"").replace(/"/g,'""') + '"';
  const csv = [cols.join(",")].concat(currentList().map(i =>
    cols.map(c => Array.isArray(i[c]) ? q2(i[c].map(f=>f.name).join("; ")) : q2(i[c])).join(","))).join("\n");
  download("download_list.csv", "\ufeff"+csv, "text/csv;charset=utf-8");
};

q.addEventListener("input", render);
stateSel.addEventListener("change", render);
platSel.addEventListener("change", render);
sortSel.addEventListener("change", render);
onlyFiles.addEventListener("change", render);
render();
</script>
</body>
</html>
"""


def render_html(items: list[dict], meta: dict, out_path: Path) -> Path:
    payload = json.dumps({"items": items, "meta": meta}, ensure_ascii=False, separators=(",", ":"))
    payload = payload.replace("</", "<\\/")  # 防止 </script> 提前闭合
    html = HTML_TEMPLATE.replace("/*__DATA__*/", payload)
    raw_label = html_escape(str(raw_dir()).replace("\\", "/"))
    html = html.replace("<code>产物根/</code>", f"<code>{raw_label}</code>")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    return out_path


def main() -> int:
    parser = argparse.ArgumentParser(description="生成下载列表查看器")
    parser.add_argument("-o", "--output", type=Path, default=DEFAULT_OUT, help="输出 HTML 路径")
    parser.add_argument("--root", type=Path, default=ROOT, help="项目根目录")
    args = parser.parse_args()

    items, meta = build_items(args.root, args.output)
    out = render_html(items, meta, args.output)
    print(f"生成 {out}（{len(items)} 条记录，{meta['diskFiles']} 个产物文件）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
