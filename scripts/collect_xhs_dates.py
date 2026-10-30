"""Collect Xiaohongshu publish dates with a signed-in browser profile.

The saved share URLs carry an ``xsec_token`` that expires within weeks, so a
fresh token is harvested from the collection page first, then every note is
opened through that link.

The publish time lives in ``.date`` and comes in three shapes:

* ``5小时前`` / ``3天前`` — relative to now
* ``10-09`` — month and day, the year has to be inferred
* ``2024-10-09`` — already complete

Usage::

    python scripts/collect_xhs_dates.py --board 6940b400000000001103e85d
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

from playwright.async_api import async_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))

from platform_meta_browser import USER_AGENT, find_browser
from runtime_paths import project_root

RELATIVE_RE = re.compile(
    r"^(\d+)\s*(秒|分钟|小时|天|周|个月|月|年)前")
FULL_DATE_RE = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})")
MONTH_DAY_RE = re.compile(r"^(\d{1,2})-(\d{1,2})")

UNIT_SECONDS = {
    "秒": 1, "分钟": 60, "小时": 3600, "天": 86400,
    "周": 604800, "个月": 2592000, "月": 2592000, "年": 31536000,
}


def parse_xhs_date(text: str, today: datetime | None = None) -> str:
    """Parse the ``.date`` text into ``YYYY-MM-DD``.

    Handles the relative form ("5小时前"), the month-day form ("10-09", year
    inferred) and the complete form ("2024-10-09"). The element may carry a
    "编辑于"/"发布于" prefix and a trailing province ("编辑于 10-15 浙江").
    Returns "" when nothing can be parsed.
    """
    if not text:
        return ""
    text = text.strip()
    # Drop the "编辑于"/"发布于" prefix and any trailing province name.
    text = re.sub(r"^(编辑于|发布于)\s*", "", text)
    text = re.split(r"\s+", text)[0]
    reference = today or datetime.now()

    match = RELATIVE_RE.match(text)
    if match:
        amount = int(match.group(1))
        seconds = UNIT_SECONDS.get(match.group(2))
        if seconds is None:
            return ""
        return (reference - timedelta(seconds=amount * seconds)).strftime(
            "%Y-%m-%d")

    match = FULL_DATE_RE.match(text)
    if match:
        return f"{int(match.group(1)):04d}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"

    match = MONTH_DAY_RE.match(text)
    if match:
        month, day = int(match.group(1)), int(match.group(2))
        if not (1 <= month <= 12 and 1 <= day <= 31):
            return ""
        year = reference.year
        try:
            candidate = datetime(year, month, day)
        except ValueError:
            return ""
        if (candidate - reference).days > 31:
            year -= 1
        return f"{year:04d}-{month:02d}-{day:02d}"
    return ""


LINK_JS = """() => Array.from(document.querySelectorAll('a[href*="xsec_token"]'))
    .map(a => a.href).filter(Boolean)"""

NOTE_JS = """() => {
    const title = document.querySelector('#detail-title');
    const date = document.querySelector('.date');
    const author = Array.from(document.querySelectorAll('a[href*="/user/profile/"]'))
        .map(a => (a.textContent || '').trim())
        .find(text => text && text !== '我' && text.length < 80) || '';
    return {
        title: title ? title.textContent.trim() : '',
        date: date ? date.textContent.trim() : '',
        uploader: author,
        path: location.pathname
    };
}"""


async def harvest_links(page, board_id: str, rounds: int, pause: float) -> dict:
    """Scroll the collection page and collect every note link it exposes."""
    url = f"https://www.xiaohongshu.com/board/{board_id}?source=web_profile_page"
    await page.goto(url, wait_until="domcontentloaded", timeout=60000)
    await page.wait_for_timeout(6000)
    found: dict[str, str] = {}
    for index in range(rounds):
        links = await page.evaluate(LINK_JS)
        for link in links:
            note_id = link.split("?")[0].rstrip("/").split("/")[-1]
            if note_id and note_id not in found:
                found[note_id] = link
        if index % 5 == 0:
            print(f"[harvest {index}] unique={len(found)}", flush=True)
        await page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(pause)
    return found


async def collect(board_id: str, out: Path, limit: int, concurrency: int,
                  delay: float, profile: Path, rounds: int,
                  only: set[str] | None = None) -> None:
    done: set[str] = set()
    if out.exists():
        for line in out.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if record.get("ok"):
                done.add(record["note_id"])

    async with async_playwright() as playwright:
        context = await playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile), headless=True,
            executable_path=find_browser(), user_agent=USER_AGENT,
            locale="zh-CN", viewport={"width": 1280, "height": 900})
        harvester = await context.new_page()
        print("harvesting note links...", flush=True)
        links = await harvest_links(harvester, board_id, rounds, 2.0)
        await harvester.close()
        print(f"harvested {len(links)} note links", flush=True)

        if only is not None:
            missing = only - set(links)
            if missing:
                print(f"warning: {len(missing)} wanted notes were not exposed "
                      f"by the board (scroll further)", flush=True)
            links = {nid: link for nid, link in links.items() if nid in only}

        targets = [(nid, link) for nid, link in links.items() if nid not in done]
        if limit:
            targets = targets[:limit]
        sem = asyncio.Semaphore(concurrency)
        log: list = []

        async def worker(note_id: str, link: str) -> None:
            async with sem:
                record = {"note_id": note_id, "url": link}
                for attempt in range(2):
                    page = await context.new_page()
                    try:
                        await page.goto(link, wait_until="domcontentloaded",
                                        timeout=60000)
                        await page.wait_for_timeout(5000)
                        data = await page.evaluate(NOTE_JS)
                        if data and data.get("title"):
                            record["title"] = data["title"]
                            if data.get("uploader"):
                                record["uploader"] = data["uploader"]
                            published = parse_xhs_date(data.get("date", ""))
                            if published:
                                record["published_at"] = published
                            record["date_text"] = data.get("date", "")
                            break
                        record["error"] = ("login_required"
                                           if data and "login" in data.get("path", "")
                                           else "no_title")
                    except Exception as exc:  # noqa: BLE001
                        record["error"] = f"{type(exc).__name__}: {str(exc)[:140]}"
                    finally:
                        await page.close()
                    if attempt == 0:
                        await asyncio.sleep(3)
                record["ok"] = bool(record.get("title"))
                record.setdefault("error", "no_title")
                with out.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(record, ensure_ascii=False) + "\n")
                log.append(record)

        started = time.time()
        tasks = [worker(nid, link) for nid, link in targets]
        for index, task in enumerate(asyncio.as_completed(tasks), 1):
            await task
            if index % 20 == 0 or index == len(tasks):
                ok = sum(1 for r in log if r.get("ok"))
                dated = sum(1 for r in log if r.get("published_at"))
                elapsed = time.time() - started
                rate = index / elapsed if elapsed else 0
                left = (len(tasks) - index) / rate if rate else 0
                print(f"[{index}/{len(tasks)}] ok={ok} dated={dated} "
                      f"fail={index - ok} {rate:.2f}/s eta={left / 60:.1f}min",
                      flush=True)
            await asyncio.sleep(delay)
        await context.close()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="采集小红书笔记的发布时间")
    parser.add_argument("--board", required=True, help="收藏夹 ID")
    parser.add_argument("--out", type=Path,
                        default=project_root() / "tmp" / "xhs_dates.jsonl")
    parser.add_argument("--profile", type=Path,
                        default=project_root() / ".chrome-xiaohongshu")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--delay", type=float, default=1.5)
    parser.add_argument("--rounds", type=int, default=40,
                        help="收藏夹滚动轮数")
    parser.add_argument("--only", type=Path, default=None,
                        help="只采集这些 ID（每行一个），通常取自状态库")
    args = parser.parse_args(argv)

    only = None
    if args.only and args.only.exists():
        only = {line.strip() for line in
                args.only.read_text(encoding="utf-8").splitlines() if line.strip()}
        print(f"restricted to {len(only)} ids", flush=True)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    asyncio.run(collect(args.board, args.out, args.limit, args.concurrency,
                        args.delay, args.profile, args.rounds, only=only))
    return 0


if __name__ == "__main__":
    sys.exit(main())