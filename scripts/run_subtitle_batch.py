"""批量下载 B 站 AI 字幕（in-process 调用统一的 subtitle_extractor）。

默认使用 Playwright；保留 _driver_patch 仅为显式 Selenium 兼容场景提供本地
ChromeDriver 回退，然后逐个 BV 调用 extract_single。

【输入输出】
输入: BV 号 JSON（fetch_search_bvids.py 的产物）
输出: <output_dir>/<BV>.md（skill 默认格式：SRT 命名但 .md 后缀）
     失败 / 跳过 / 成功 列表写到 .batch/<kw>_results.json

【用法】
  python run_subtitle_batch.py [<bvids_json>] [<output_dir>] [<browser>]
  默认: 跑最近一次 fetch_search_bvids.py 的产物
"""
import io
import json
import sys
import time
from argparse import ArgumentParser
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Optional, Tuple

from _driver_patch import get_skill_dir, patch_selenium_edge
from runtime_paths import default_output_dir

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# === 默认路径 ===
SKILL_DIR = get_skill_dir()
# scripts/ → bilibili-subtitle-fetch/ → skills/ → .claude/ → project_root
DEFAULT_BVIDS_JSON = (
    default_output_dir() / ".batch" / "hyperframes_100_bvids.json"
)
DEFAULT_OUTPUT_DIR = default_output_dir()
SKILL_MODULE = "subtitle_extractor"
DEFAULT_BROWSER = "chrome"


def load_skill_module():
    """导入本仓库的 subtitle_extractor。"""
    if SKILL_DIR.as_posix() not in sys.path:
        sys.path.insert(0, SKILL_DIR.as_posix())
    return __import__(SKILL_MODULE)


def run_one(extractor, bvid: str) -> Tuple[str, str, Optional[str]]:
    """跑一条视频。返回 (status, message, error_code)。"""
    buf_out = io.StringIO()
    buf_err = io.StringIO()
    try:
        with redirect_stdout(buf_out), redirect_stderr(buf_err):
            result = extractor.extract_single(bvid)
    except Exception as e:
        from bili_guard import classify_failure
        return (
            "failed",
            f"exception: {type(e).__name__}: {str(e)[:200]}",
            classify_failure(error=e),
        )

    out = buf_out.getvalue()
    if result == "SKIPPED":
        return "skipped", "already in download_list", None
    if result:
        return "success", str(result), None
    err_tail = buf_err.getvalue()[-200:] or out[-200:]
    return (
        "failed",
        f"no subtitle captured. tail: {err_tail}",
        getattr(extractor, "last_failure_kind", None),
    )


def main() -> int:
    parser = ArgumentParser(description="批量下载 B 站 AI 字幕")
    parser.add_argument("bvids_json", nargs="?", type=Path, default=DEFAULT_BVIDS_JSON)
    parser.add_argument("output_dir", nargs="?", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("browser", nargs="?", choices=("edge", "chrome"), default=DEFAULT_BROWSER)
    parser.add_argument("--backend", choices=("playwright", "selenium"), default="playwright")
    parser.add_argument("--min-delay", type=float, default=8)
    parser.add_argument("--max-delay", type=float, default=15)
    parser.add_argument("--rate-limit-threshold", type=int, default=2)
    parser.add_argument("--cooldown-seconds", type=int, default=900)
    args = parser.parse_args()
    if args.min_delay < 0 or args.max_delay < args.min_delay:
        parser.error("必须满足 0 <= min-delay <= max-delay")
    if args.rate_limit_threshold < 1 or args.cooldown_seconds < 0:
        parser.error("rate-limit-threshold 必须 >= 1，cooldown-seconds 必须 >= 0")
    bvids_json = args.bvids_json.resolve()
    output_dir = args.output_dir.resolve()
    browser = args.browser

    if not bvids_json.exists():
        print(f"[fatal] BV 列表不存在: {bvids_json}")
        return 1

    with open(bvids_json, encoding="utf-8") as f:
        data = json.load(f)
    videos = data.get("videos", [])
    keyword = data.get("keyword", "batch")

    print(
        f"[start] {len(videos)} videos, output={output_dir}, browser={browser}"
    )
    print("[info] compatibility patch + extractor import...")

    # Selenium 兼容回退：默认 Playwright 不依赖此 patch。
    if patch_selenium_edge():
        print("[info] local Chrome driver patch enabled")
    else:
        print(f"[info] using Selenium Manager for {browser.title()}")
    # 加载统一提取器
    se = load_skill_module()
    # 3. 构造 extractor
    extractor = se.SubtitleExtractor(
        output_dir=output_dir,
        enrich_with_meta=False,  # 与本项目 10_raw/ 现有 .txt 风格一致（无 frontmatter）
        browser=browser,
        backend=args.backend,
        reuse_browser=True,
        min_delay=args.min_delay,
        max_delay=args.max_delay,
        rate_limit_threshold=args.rate_limit_threshold,
        cooldown_seconds=args.cooldown_seconds,
    )

    try:
        results = {
        "keyword": keyword,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "browser": browser,
        "total": len(videos),
        "success": [],
        "skipped": [],
        "failed": [],
        "guard": {
            "rate_limit_threshold": args.rate_limit_threshold,
            "cooldown_seconds": args.cooldown_seconds,
            "state_file": str(output_dir / ".bili_guard_state.json"),
        },
        "browser_start_count": 0,
        "browser_restart_count": 0,
    }

        t0 = time.time()
        pause_code = 0
        pause_reason = None
        pause_exit_codes = {
        "RATE_LIMITED": 10,
        "LOGIN_REQUIRED": 11,
        "BROWSER_START_FAILED": 12,
        }
        for i, v in enumerate(videos, 1):
            bvid = v["bvid"]
            title = str(v.get("title") or "")
            title_short = title[:30].replace("\n", " ")
            print(f"\n[{i}/{len(videos)}] {bvid} - {title_short}", flush=True)
            status, msg, error_code = run_one(extractor, bvid)
            from bili_guard import normalize_subtitle_probe
            results[status].append({
            "bvid": bvid,
            "title": title,
            "title_source": v.get("title_source", "unresolved" if not title else "card"),
            "msg": msg,
            "probe": normalize_subtitle_probe(getattr(extractor, "last_probe", None)),
            "duration_sec": getattr(extractor, "last_duration_sec", 0.0),
            })
            if error_code:
                results[status][-1]["error_code"] = error_code
            elapsed = time.time() - t0
            rate = i / elapsed if elapsed > 0 else 0
            eta = (len(videos) - i) / rate if rate > 0 else 0
            print(
            f"  -> {status}: {msg[:80]} "
            f"[{elapsed:.0f}s elapsed, ETA {eta/60:.1f}min]",
            flush=True,
            )
            breaker_open = getattr(getattr(extractor, "breaker", None), "state", None) == "OPEN"
            should_pause = error_code in (
            "LOGIN_REQUIRED",
            "BROWSER_START_FAILED",
            ) or (error_code == "RATE_LIMITED" and breaker_open)
            if should_pause:
                pause_code = pause_exit_codes[error_code]
                pause_reason = error_code
                print(f"[pause] {error_code}; unvisited videos remain pending")
                break

        results["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        results["elapsed_sec"] = round(time.time() - t0, 1)
        results["exit_code"] = pause_code or (0 if not results["failed"] else 2)
        results["pause_reason"] = pause_reason
        results["browser_start_count"] = getattr(extractor, "browser_start_count", 0)
        results["browser_restart_count"] = getattr(extractor, "browser_restart_count", 0)

        log_path = bvids_json.parent / f"{keyword}_results.json"
        with open(log_path, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)

        print("\n" + "=" * 60)
        print(f"SUCCESS : {len(results['success'])}")
        print(f"SKIPPED : {len(results['skipped'])}")
        print(f"FAILED  : {len(results['failed'])}")
        print(f"TOTAL   : {len(videos)}")
        print(f"ELAPSED : {results['elapsed_sec']}s")
        print(f"LOG     : {log_path}")
        print("=" * 60)

        if results["failed"]:
            print("\n失败 BV（可能无 AI 字幕 / 网络 / 超时）:")
            for f in results["failed"][:30]:
                print(f"  - {f['bvid']}: {f['msg'][:100]}")
            if len(results["failed"]) > 30:
                print(f"  ... 还有 {len(results['failed']) - 30} 条见 {log_path}")

        return results["exit_code"]
    finally:
        getattr(extractor, "close", lambda: None)()


if __name__ == "__main__":
    sys.exit(main())
