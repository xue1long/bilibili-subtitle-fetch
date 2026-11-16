"""按 JSON 清单批量下载小红书图文笔记（合集 / board 页链接清单）。

清单格式：[{"id": "<24 位笔记ID>", "token": "<xsec_token>", "title": "可选标题"}]
链接由外部准备（例如接管浏览器从合集页读出），本脚本不负责抓取页面。

断点续跑以产物目录下 <id>/<id>.md 是否存在为准（见 docs/adr/0002），已存在的
条目跳过；视频笔记在写任何文件之前拦下，不跑 ASR。逐条结果写 --report，
进程中断后直接重跑即可。

用法：
    python scripts/xhs_board_batch.py --notes .scratch/board_notes.json
"""

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import xiaohongshu_cli as cli
import xiaohongshu_media as media
from runtime_paths import raw_dir


class SkipNote(Exception):
    """非图文笔记，跳过。"""


def note_url(item: dict, source: str = "web_board_page") -> str:
    """清单只存 id + token：归一化成 /explore/<笔记ID>，避开 board 两段 ID 歧义。"""
    return (
        f"https://www.xiaohongshu.com/explore/{item['id']}"
        f"?xsec_token={item.get('token', '')}&xsec_source=pc_user&source={source}"
    )


def _discover_images_only(url, profile, original=None):
    """复用项目发现逻辑，把视频笔记在下载前拦下（cli.run 内部按名字引用它）。"""
    metadata, video_url, image_urls = (original or media.discover_note)(url, profile)
    if video_url:
        raise SkipNote("video note")
    return metadata, video_url, image_urls


def run_batch(notes, output, report, delay=1.5, runner=None, source="web_board_page"):
    """按清单下载，返回 {status: count}；runner 可注入以便测试，默认调用 cli.run。"""
    runner = runner or cli.run
    output.mkdir(parents=True, exist_ok=True)
    report.parent.mkdir(parents=True, exist_ok=True)
    original_discover = cli.discover_note
    cli.discover_note = lambda url, profile: _discover_images_only(
        url, profile, original_discover
    )
    results = []
    try:
        for index, item in enumerate(notes, 1):
            note_id = item["id"]
            final_dir = output / note_id
            record = {"index": index, "id": note_id, "title": item.get("title", "")}
            if (final_dir / f"{note_id}.md").exists():
                record["status"] = "skipped_exists"
            else:
                try:
                    runner(note_url(item, source), output, False, "small")
                    images = sorted((final_dir / "images").glob("*.webp"))
                    record.update(
                        status="ok",
                        images=len(images),
                        path=str(final_dir / f"{note_id}.md"),
                    )
                except Exception as exc:
                    if isinstance(exc, SkipNote) or isinstance(exc.__cause__, SkipNote):
                        record["status"] = "skipped_video"
                    else:
                        record.update(
                            status="failed",
                            code=getattr(exc, "code", type(exc).__name__),
                            error=str(exc),
                        )
                if delay:
                    time.sleep(delay)
            results.append(record)
            print(f"[{index}/{len(notes)}] {note_id} {record['status']}", flush=True)
            report.write_text(
                json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
            )
    finally:
        cli.discover_note = original_discover
    summary = {}
    for record in results:
        summary[record["status"]] = summary.get(record["status"], 0) + 1
    return summary


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="按 JSON 清单批量下载小红书图文笔记")
    parser.add_argument("--notes", type=Path, required=True, help="清单 JSON：[{id, token, title?}]")
    parser.add_argument("--output", "-o", type=Path, default=raw_dir() / "03_小红书")
    parser.add_argument("--report", type=Path, help="逐条结果 JSON，默认 <清单名>_report.json")
    parser.add_argument("--delay", type=float, default=1.5, help="每条之间的间隔秒数")
    parser.add_argument("--source", default="web_board_page", help="URL 里的 source 参数")
    args = parser.parse_args(argv)
    notes = json.loads(args.notes.read_text(encoding="utf-8"))
    if not isinstance(notes, list):
        parser.error("--notes 必须是 JSON 数组")
    report = args.report or args.notes.with_name(args.notes.stem + "_report.json")
    summary = run_batch(notes, args.output, report, args.delay, source=args.source)
    print("SUMMARY " + json.dumps(summary, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
