"""Unified entry point for single video, favorite list, and uploader space jobs."""
import argparse
import sys
from pathlib import Path

from sources.favorite import discover as discover_favorite
from sources.single_video import discover as discover_single
from sources.space import discover as discover_space
from pipeline.planner import plan
from pipeline.models import TaskStatus
from runtime_paths import project_root
from storage.manifest import manifest_path, upsert
from storage.state_store import load as load_state, state_path, update as update_state
from storage.sqlite_store import SQLiteStore
from subtitle_extractor import SubtitleExtractor


def main(argv=None):
    parser = argparse.ArgumentParser(description="统一 B 站字幕提取入口")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--video")
    group.add_argument("--favorite-url")
    group.add_argument("--space-url")
    parser.add_argument("--output", "-o")
    parser.add_argument("--browser", choices=("chrome", "edge"), default="chrome")
    parser.add_argument("--backend", choices=("playwright", "selenium"), default="playwright")
    parser.add_argument("--asr-fallback", action="store_true")
    parser.add_argument("--asr-model", default="small")
    parser.add_argument("--no-meta", action="store_true")
    parser.add_argument("--min-delay", type=float, default=8)
    parser.add_argument("--max-delay", type=float, default=15)
    parser.add_argument("--retry-paused", action="store_true",
                        help="重新处理之前因登录、限流或浏览器问题暂停的任务")
    parser.add_argument("--retry-failed", action="store_true",
                        help="只重新处理之前失败或没有原生字幕的任务")
    parser.add_argument("--only-status", choices=("queued", "failed", "paused", "no_subtitle"),
                        help="只处理指定状态的任务")
    args = parser.parse_args(argv)

    if args.video:
        videos = discover_single(args.video)
    elif args.favorite_url:
        print(f"[收藏夹同步] {args.favorite_url}")
        videos = discover_favorite(args.favorite_url)
    else:
        print(f"[空间同步] {args.space_url}")
        videos = discover_space(args.space_url)
    manifest = upsert(manifest_path(project_root()), videos)
    print(f"[来源记录] {manifest_path(project_root())}，累计 {len(manifest['videos'])} 个视频")
    extractor = SubtitleExtractor(
        output_dir=Path(args.output) if args.output else None,
        browser=args.browser, backend=args.backend,
        asr_fallback=args.asr_fallback, asr_model=args.asr_model,
        enrich_with_meta=not args.no_meta,
        min_delay=args.min_delay, max_delay=args.max_delay,
    )
    try:
        legacy_path = extractor.output_dir / "download_list.json"
        jobs_path = state_path(project_root())
        store = SQLiteStore(project_root() / "data" / "subtitle_tasks.db")
        store.migrate_json(jobs_path, legacy_path)
        store.upsert_videos(videos)
        tasks = plan(videos, store.jobs())
        if args.only_status:
            tasks = [task for task in tasks if task.status.value == args.only_status]
        elif args.retry_failed:
            retry_statuses = {TaskStatus.QUEUED, TaskStatus.NO_SUBTITLE, TaskStatus.FAILED}
            tasks = [task for task in tasks if task.status in retry_statuses or task.reason]
        if not args.retry_paused:
            tasks = [task for task in tasks if task.status != TaskStatus.PAUSED]
        print(f"[发现] {len(videos)} 个视频，待处理 {len(tasks)} 个")
        success = 0
        for index, task in enumerate(tasks, 1):
            video = task.video
            print(f"[{index}/{len(tasks)}] {video.bvid} {video.title[:40]}")
            store.update_job(video.bvid, "native_running")
            result = extractor.extract_single(video.bvid)
            if result not in (None, "SKIPPED"):
                success += 1
                source = "asr" if extractor._download_list.get("records", {}).get(video.bvid, {}).get("source") == "asr" else "native"
                store.update_job(video.bvid, "asr_success" if source == "asr" else "native_success", source=source)
            elif result is None:
                store.update_job(video.bvid, "paused" if extractor.last_failure_kind in ("LOGIN_REQUIRED", "RATE_LIMITED", "BROWSER_START_FAILED") else "failed", error=extractor.last_failure_message)
            if result is None and extractor.last_failure_kind in ("LOGIN_REQUIRED", "RATE_LIMITED", "BROWSER_START_FAILED"):
                break
        print(f"[完成] 成功处理 {success}/{len(videos)}")
        return 0 if extractor.last_failure_kind not in ("LOGIN_REQUIRED", "RATE_LIMITED", "BROWSER_START_FAILED") else 1
    finally:
        extractor.close()


if __name__ == "__main__":
    sys.exit(main())
