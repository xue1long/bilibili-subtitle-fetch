"""抓 B 站搜索结果页的 BV 号列表（Playwright 驱动 Edge）。

输入: keyword (默认 "hyperframes")，target 数量 (默认 100)，输出目录
输出: <output_dir>/.batch/<keyword>_<target>_bvids.json

策略: B 站 2023 后对搜索 API 加重度风控（wbi + cookie + fingerprint），
裸 HTTP 即使带上 wbi 签名也常被 412 挡。
本脚本用 Playwright 控制本地 Edge 渲染搜索页，从 DOM 抓 BV 号。

依赖:
  pip install playwright    (1.60.0 已验证可用)
  Playwright chromium       (默认会装在 %LOCALAPPDATA%/ms-playwright/)

用法:
  python fetch_search_bvids.py [<keyword> <target> <output_dir>]
  默认 keyword=hyperframes  target=100  output_dir=10_raw/01_B站视频转录
"""
import json
import re
import sys
import time
from argparse import ArgumentParser
from pathlib import Path
from typing import Any, Dict, List, Set
from urllib.parse import quote_plus

from runtime_paths import chrome_profile_dir, default_output_dir, edge_profile_dir
from bili_guard import CircuitBreaker, FailureKind, RateLimiter, check_login_state

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# === 默认输出目录：项目根 / 10_raw / 01_B站视频转录 ===
SKILL_DIR = Path(__file__).resolve().parent
# scripts/ → bilibili-subtitle-fetch/ → skills/ → .claude/ → project_root
DEFAULT_OUTPUT_DIR = default_output_dir()
DEFAULT_BROWSER = "chrome"

BATCH_SUBDIR = ".batch"
PAGE_SIZE = 20

SEARCH_URL = "https://search.bilibili.com/all?keyword={kw}&page={page}"
BV_HREF_RE = re.compile(r"/video/(BV[\w]+)")
EM_TAG_RE = re.compile(r"<[^>]+>")
METRIC_TOKEN_RE = re.compile(
    r"^(?:\d+(?:\.\d+)?(?:万|亿)?(?:播放|评论|弹幕|次)?|\d{1,2}:\d{2}|\d{1,2}:\d{2}:\d{2}|第?\d+名?)$"
)
COUNT_LABEL_RE = re.compile(
    r"^(?:播放|评论|弹幕|点赞|收藏)\s*\d+(?:\.\d+)?(?:万|亿)?$|^\d+(?:\.\d+)?(?:万|亿)?\s*(?:播放|评论|弹幕|点赞|收藏|次)$"
)
RANKING_RE = re.compile(
    r"^(?:排名|排行|第\s*)\s*\d+\s*(?:名|名次)?$|^\d+\s*(?:名|名次|位)$|^TOP\s*\d+$",
    re.I,
)
PLATFORM_SUFFIX_RE = re.compile(r"(?:[\s_-]+(?:哔哩哔哩|bilibili))+$", re.I)
GENERIC_DETAIL_LABELS = {
    "登录",
    "login",
    "bilibili",
    "风险控制",
    "风控",
    "访问受限",
    "验证",
    "出错",
}


def _normalize_title(raw_title: str) -> str:
    return re.sub(r"\s+", " ", EM_TAG_RE.sub("", raw_title or "")).strip()


def is_metric_title(title: str) -> bool:
    """Return whether a title contains only card metrics/ranking text."""
    normalized = _normalize_title(title)
    if not normalized:
        return False
    if (
        re.fullmatch(r"\d{1,2}:\d{2}(?::\d{2})?", normalized)
        or COUNT_LABEL_RE.fullmatch(normalized)
        or RANKING_RE.fullmatch(normalized)
    ):
        return True
    tokens = normalized.split(" ")
    return len(tokens) > 1 and all(METRIC_TOKEN_RE.fullmatch(token) for token in tokens)


def clean_search_title(raw_title: str) -> str:
    title = _normalize_title(raw_title)
    return "" if is_metric_title(title) else title


def _usable_detail_title(raw_title: str) -> str:
    title = clean_search_title(raw_title)
    if not title:
        return ""
    core = PLATFORM_SUFFIX_RE.sub("", title).strip(" -_|").lower()
    if core in GENERIC_DETAIL_LABELS:
        return ""
    if core.startswith("哔哩哔哩") and not re.sub(
        r"[哔哩干杯つロ\s\W_]", "", core
    ):
        return ""
    return title


def resolve_detail_title(page, bvid: str, limiter) -> str:
    if limiter is None:
        return ""
    temporary_page = None
    try:
        limiter.wait()
        temporary_page = page.context.new_page()
        temporary_page.goto(
            f"https://www.bilibili.com/video/{bvid}",
            wait_until="domcontentloaded",
            timeout=30000,
        )
        title = _usable_detail_title(temporary_page.title())
        if title:
            return title
        state_title = temporary_page.evaluate(
            "window.__INITIAL_STATE__?.videoData?.title || ''"
        )
        return _usable_detail_title(state_title)
    except Exception:
        return ""
    finally:
        if temporary_page is not None:
            try:
                temporary_page.close()
            except Exception:
                pass


def fetch_page(page, keyword: str, page_num: int, limiter) -> List[Dict[str, Any]]:
    """加载一页搜索结果，等候渲染后提取 BV 号 + 标题。"""
    if limiter is None:
        raise ValueError("limiter is required")
    url = SEARCH_URL.format(kw=quote_plus(keyword), page=page_num)
    limiter.wait()
    page.goto(url, wait_until="domcontentloaded", timeout=30000)
    try:
        page.wait_for_selector("a[href*='/video/BV']", timeout=20000)
    except Exception:
        return []
    time.sleep(1.5)  # 留 1.5s 让所有 card 渲染完

    videos: List[Dict[str, Any]] = []
    seen_bv: Set[str] = set()
    anchors = page.query_selector_all("a[href*='/video/BV']")
    for a in anchors:
        href = a.get_attribute("href") or ""
        m = BV_HREF_RE.search(href)
        if not m:
            continue
        bvid = m.group(1)
        if bvid in seen_bv:
            continue
        seen_bv.add(bvid)
        title = ""
        for attr in ("title", "aria-label", "data-title"):
            candidate = a.get_attribute(attr) or ""
            if candidate:
                title = clean_search_title(candidate)
                if title:
                    break
        if not title:
            try:
                title = clean_search_title(a.inner_text())
            except Exception:
                title = ""
        title_source = "card" if title else "unresolved"
        if not title:
            title = resolve_detail_title(page, bvid, limiter)
            title_source = "detail" if title else "unresolved"
        videos.append(
            {
                "bvid": bvid,
                "title": title,
                "title_source": title_source,
                "author": "",
                "play": 0,
                "duration": "",
                "pubdate": 0,
                "url": f"https://www.bilibili.com/video/{bvid}",
            }
        )
    return videos


def main() -> int:
    parser = ArgumentParser(description="抓取 B 站搜索结果中的 BV 号")
    parser.add_argument("keyword", nargs="?", default="hyperframes")
    parser.add_argument("target", nargs="?", type=int, default=100)
    parser.add_argument("output_dir", nargs="?", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--browser", choices=("chrome", "edge"), default=DEFAULT_BROWSER)
    args = parser.parse_args()
    if args.target < 1:
        parser.error("target 必须是正整数")

    keyword = args.keyword
    target = args.target
    output_dir = args.output_dir.resolve()

    batch_dir = output_dir / BATCH_SUBDIR
    batch_dir.mkdir(parents=True, exist_ok=True)
    out_path = batch_dir / f"{keyword}_{target}_bvids.json"

    browser = args.browser
    profile_dir = chrome_profile_dir() if browser == "chrome" else edge_profile_dir()
    channel = "chrome" if browser == "chrome" else "msedge"
    print(f"[start] keyword={keyword!r} target={target} (Playwright + {browser.title()})")

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            channel=channel,
            headless=False,
        )
        try:
            page_obj = browser.new_page()
            page_obj.set_viewport_size({"width": 1440, "height": 900})
            page_obj.goto("https://www.bilibili.com/", wait_until="domcontentloaded", timeout=30000)

            session = check_login_state(page_obj)
            if not session.ok:
                CircuitBreaker(output_dir / ".bili_guard_state.json").record(session.reason)
                print(f"[pause] {session.reason}; 请先在 {browser.title()} 登录 B 站后重试")
                return 11 if session.reason == FailureKind.LOGIN_REQUIRED else 12

            seen: Set[str] = set()
            collected: List[Dict[str, Any]] = []
            limiter = RateLimiter()
            page_num = 1
            max_pages = (target + PAGE_SIZE - 1) // PAGE_SIZE + 2

            while len(collected) < target and page_num <= max_pages:
                videos = fetch_page(page_obj, keyword, page_num, limiter)
                if not videos:
                    print(f"[info] page {page_num}: empty, stop")
                    break
                added = 0
                for v in videos:
                    if v["bvid"] in seen:
                        continue
                    seen.add(v["bvid"])
                    collected.append(v)
                    added += 1
                print(
                    f"[page {page_num}] got={len(videos)} new={added} "
                    f"total={len(collected)}/{target}"
                )
                if added == 0:
                    print(f"[info] page {page_num}: no new videos, stop")
                    break
                page_num += 1
                time.sleep(1.5)
        finally:
            try:
                browser.close()
            except Exception:
                pass

    collected = collected[:target]
    payload = {
        "keyword": keyword,
        "target": target,
        "fetched": len(collected),
        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "videos": collected,
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(
        f"\n[done] {len(collected)}/{target} BV 号已写入 {out_path}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
