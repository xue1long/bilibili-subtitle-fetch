"""Browser fallbacks for platforms whose metadata is not available locally.

Both platforms serve JS-rendered pages, so plain HTTP requests return either a
JS-VM shell (Douyin) or nothing useful. A headless browser is required.

Two platform-specific details that cost real debugging time:

* Douyin renders ``<meta name="description">`` late — waiting under ~10s
  yields an empty tag. The description also contains raw newlines, which
  truncate a naive ``[^"]*`` match, so the pattern is permissive and the
  captured value is normalised afterwards.
* Bilibili wraps its state in an IIFE
  (``window.__INITIAL_STATE__={};(function(){...})();``), so a regex can never
  reach ``videoData``. The state is read from inside the page instead.
* Xiaohongshu redirects every request to ``/login`` without a signed-in
  profile, and the ``xsec_token`` saved with a note expires within weeks. A
  logged-in persistent profile is therefore required, and each note has to be
  opened through the share URL recorded in the local note list.

Results are appended to a JSONL cache; rows that failed are not marked done
and stay retryable.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

from playwright.async_api import async_playwright

from runtime_paths import project_root

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36")

# Prefer a real Chrome/Edge binary over a downloaded Chromium build.
BROWSER_CANDIDATES = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
)

DOUYIN_DESC_RE = re.compile(
    r'<meta[^>]+name="description"[^>]*content="(.*?)"\s*/?>', re.S)
DOUYIN_KEYWORDS_RE = re.compile(
    r'<meta[^>]+name="keywords"[^>]*content="(.*?)"\s*/?>', re.S)
DOUYIN_HEAD_RE = re.compile(r"^(.*?)\s*-\s*(.+?)于(\d{8})发布在抖音")
DOUYIN_LIKES_RE = re.compile(r"收获了([\d.]+)(万)?个喜欢")

BILI_STATE_JS = """() => {
    const v = window.__INITIAL_STATE__ && window.__INITIAL_STATE__.videoData;
    if (!v) return null;
    return {
        title: v.title || '',
        uploader: (v.owner && v.owner.name) || '',
        pubdate: v.pubdate || 0,
        view: (v.stat && v.stat.view) || 0,
        like: (v.stat && v.stat.like) || 0
    };
}"""

# Xiaohongshu renders the title, author and publish time in the DOM.
XHS_STATE_JS = """() => {
    const title = document.querySelector('#detail-title');
    const author = Array.from(document.querySelectorAll('a[href*="/user/profile/"]'))
        .map(a => (a.innerText || '').trim())
        .find(text => text && text !== '我' && text.length < 80) || '';
    const body = document.body ? document.body.innerText : '';
    // The note shows "编辑于 03-15" or "发布于 03-15" (no year).
    const dated = body.match(/(?:编辑于|发布于)?\\s*(\\d{1,2})-(\\d{1,2})\\s*(发布于|编辑于)?/);
    let published = '';
    if (dated) {
        published = dated[1] + '-' + dated[2];
    }
    return {
        title: title ? title.innerText.trim() : '',
        uploader: author,
        published_md: published,
        path: location.pathname
    };
}"""


def find_browser() -> str | None:
    """Return the first installed Chrome/Edge, or None for the bundled build."""
    import os
    for candidate in BROWSER_CANDIDATES:
        if os.path.exists(candidate):
            return candidate
    return None


def _clean_meta(value: str) -> str:
    for src, dst in (("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
                     ("&quot;", '"'), ("&#39;", "'"), ("&nbsp;", " ")):
        value = value.replace(src, dst)
    return re.sub(r"\s+", " ", value).strip()


def parse_douyin_description(description: str) -> dict:
    """Split a Douyin description into title/uploader/date/likes.

    The description looks like::

        <caption> - <nickname>于<yyyymmdd>发布在抖音，已经收获了<n>万个喜欢，来抖音，记录美好生活！
    """
    out: dict = {}
    if not description:
        return out
    match = DOUYIN_HEAD_RE.match(description)
    if not match:
        return out
    body, uploader, date = (m.strip() for m in match.groups())
    if body:
        out["title"] = body
    if uploader:
        out["uploader"] = uploader
    if date:
        out["published_at"] = f"{date[:4]}-{date[4:6]}-{date[6:]}"
    likes = DOUYIN_LIKES_RE.search(description)
    if likes:
        try:
            count = float(likes.group(1))
            out["like_count"] = int(count * 10000) if likes.group(2) else int(count)
        except ValueError:
            pass
    return out


def resolve_xhs_date(month_day: str, today: str | None = None) -> str:
    """Turn a Xiaohongshu "MM-DD" stamp into a full date.

    The note only shows month and day, so the year is inferred: a date more
    than a month in the future belongs to the previous year.
    """
    if not month_day:
        return ""
    match = re.match(r"^(\d{1,2})-(\d{1,2})$", month_day.strip())
    if not match:
        return ""
    month, day = int(match.group(1)), int(match.group(2))
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return ""
    reference = datetime.strptime(today, "%Y-%m-%d") if today else datetime.now()
    year = reference.year
    try:
        candidate = datetime(year, month, day)
    except ValueError:
        return ""
    if (candidate - reference).days > 31:
        year -= 1
    return f"{year:04d}-{month:02d}-{day:02d}"


def load_done(path: Path) -> set[str]:
    """Ids already cached successfully; failed rows stay retryable."""
    done: set[str] = set()
    if not path.exists():
        return done
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if record.get("ok"):
            done.add(record["video_id"])
    return done


async def _fetch_douyin(ctx, sem, video_id: str, out: Path, log: list) -> None:
    async with sem:
        record = {"video_id": video_id}
        for attempt in range(2):
            page = await ctx.new_page()
            try:
                await page.goto(f"https://www.douyin.com/video/{video_id}",
                                wait_until="domcontentloaded", timeout=60000)
                # The SSR meta tag is injected late; 5.5s was not enough.
                await page.wait_for_timeout(11000)
                html = await page.content()
                match = DOUYIN_DESC_RE.search(html)
                if match:
                    record.update(parse_douyin_description(
                        _clean_meta(match.group(1))))
                keywords = DOUYIN_KEYWORDS_RE.search(html)
                if keywords:
                    record["keywords"] = [
                        t.strip() for t in _clean_meta(
                            keywords.group(1)).split(",") if t.strip()]
                if record.get("title"):
                    break
                record["error"] = "no_desc_meta"
            except Exception as exc:  # noqa: BLE001 - network errors vary widely
                record["error"] = f"{type(exc).__name__}: {str(exc)[:150]}"
            finally:
                await page.close()
            if attempt == 0:
                await asyncio.sleep(3)
        record["url"] = f"https://www.douyin.com/video/{video_id}"
        record["ok"] = bool(record.get("title"))
        record.setdefault("error", "no_desc_meta")
        _append(out, record)
        log.append(record)


async def _fetch_bilibili(ctx, sem, video_id: str, out: Path, log: list) -> None:
    async with sem:
        record = {"video_id": video_id}
        for attempt in range(2):
            page = await ctx.new_page()
            try:
                await page.goto(f"https://www.bilibili.com/video/{video_id}",
                                wait_until="domcontentloaded", timeout=60000)
                await page.wait_for_timeout(6000)
                data = await page.evaluate(BILI_STATE_JS)
                if data and data.get("title"):
                    record["title"] = data["title"]
                    record["uploader"] = data.get("uploader", "")
                    if data.get("pubdate"):
                        record["published_at"] = time.strftime(
                            "%Y-%m-%d", time.localtime(data["pubdate"]))
                    if data.get("view"):
                        record["view_count"] = data["view"]
                    if data.get("like"):
                        record["like_count"] = data["like"]
                    break
                element = await page.query_selector("h1.video-title")
                if element:
                    record["title"] = (await element.inner_text()).strip()
                owner = await page.query_selector(".up-name")
                if owner:
                    record["uploader"] = (await owner.inner_text()).strip()
                if record.get("title"):
                    break
                record["error"] = "no_title"
            except Exception as exc:  # noqa: BLE001 - network errors vary widely
                record["error"] = f"{type(exc).__name__}: {str(exc)[:150]}"
            finally:
                await page.close()
            if attempt == 0:
                await asyncio.sleep(3)
        record["url"] = f"https://www.bilibili.com/video/{video_id}"
        record["ok"] = bool(record.get("title"))
        record.setdefault("error", "no_title")
        _append(out, record)
        log.append(record)


async def _fetch_xiaohongshu(ctx, sem, video_id: str, url: str,
                             out: Path, log: list) -> None:
    async with sem:
        record = {"video_id": video_id, "url": url}
        for attempt in range(2):
            page = await ctx.new_page()
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=60000)
                await page.wait_for_timeout(5000)
                data = await page.evaluate(XHS_STATE_JS)
                if data and data.get("title"):
                    record["title"] = data["title"]
                    if data.get("uploader"):
                        record["uploader"] = data["uploader"]
                    published = resolve_xhs_date(data.get("published_md", ""))
                    if published:
                        record["published_at"] = published
                    break
                record["error"] = ("login_required"
                                   if data and "login" in data.get("path", "")
                                   else "no_title")
            except Exception as exc:  # noqa: BLE001 - network errors vary widely
                record["error"] = f"{type(exc).__name__}: {str(exc)[:150]}"
            finally:
                await page.close()
            if attempt == 0:
                await asyncio.sleep(3)
        record["ok"] = bool(record.get("title"))
        record.setdefault("error", "no_title")
        _append(out, record)
        log.append(record)


def _append(out: Path, record: dict) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


async def _run(platform: str, ids: list[str], out: Path, concurrency: int,
               delay: float, urls: dict[str, str] | None = None,
               profile_dir: Path | None = None) -> None:
    sem = asyncio.Semaphore(concurrency)
    log: list = []
    async with async_playwright() as playwright:
        if profile_dir:
            # Xiaohongshu needs a signed-in session; the persistent profile
            # keeps the cookies between runs.
            context = await playwright.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir), headless=True,
                user_agent=USER_AGENT, locale="zh-CN",
                viewport={"width": 1280, "height": 900})
        else:
            browser = await playwright.chromium.launch(
                headless=True, executable_path=find_browser())
            context = await browser.new_context(
                user_agent=USER_AGENT, locale="zh-CN",
                viewport={"width": 1280, "height": 900})
        if platform == "xiaohongshu":
            tasks = [
                _fetch_xiaohongshu(context, sem, vid,
                                   (urls or {}).get(vid,
                                       f"https://www.xiaohongshu.com/explore/{vid}"),
                                   out, log)
                for vid in ids
            ]
        else:
            worker = _fetch_douyin if platform == "douyin" else _fetch_bilibili
            tasks = [worker(context, sem, vid, out, log) for vid in ids]
        started = time.time()
        for index, task in enumerate(asyncio.as_completed(tasks), 1):
            await task
            if index % 20 == 0 or index == len(ids):
                ok = sum(1 for r in log if r.get("ok"))
                elapsed = time.time() - started
                rate = index / elapsed if elapsed else 0
                left = (len(ids) - index) / rate if rate else 0
                print(f"[{index}/{len(ids)}] ok={ok} fail={index - ok} "
                      f"{rate:.2f}/s eta={left / 60:.1f}min", flush=True)
            await asyncio.sleep(delay)
        await context.close()
        if not profile_dir:
            await browser.close()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="抓取抖音/B站/小红书元数据并缓存到 JSONL")
    parser.add_argument("--platform",
                        choices=("douyin", "bilibili", "xiaohongshu"),
                        required=True)
    parser.add_argument("--ids", type=Path, required=True,
                        help="文本文件，每行一个视频 ID")
    parser.add_argument("--out", type=Path, required=True,
                        help="JSONL 缓存文件")
    parser.add_argument("--urls", type=Path, default=None,
                        help="ID→URL 映射 JSON（小红书需要带 xsec_token 的分享链接）")
    parser.add_argument("--profile", type=Path, default=None,
                        help="持久化浏览器 profile 目录（小红书必须登录）")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--delay", type=float, default=1.5)
    args = parser.parse_args(argv)

    ids = [line.strip() for line in
           args.ids.read_text(encoding="utf-8").splitlines() if line.strip()]
    done = load_done(args.out)
    ids = [vid for vid in ids if vid not in done]
    if args.limit:
        ids = ids[:args.limit]
    if not ids:
        print("nothing to fetch")
        return 0
    urls = {}
    if args.urls and args.urls.exists():
        urls = json.loads(args.urls.read_text(encoding="utf-8"))
    print(f"to fetch: {len(ids)} (cached {len(done)})", flush=True)
    asyncio.run(_run(args.platform, ids, args.out, args.concurrency, args.delay,
                     urls=urls, profile_dir=args.profile))
    return 0


if __name__ == "__main__":
    sys.exit(main())
