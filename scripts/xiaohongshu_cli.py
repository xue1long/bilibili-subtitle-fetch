"""Download one Xiaohongshu note and write its metadata manifest."""

import argparse
import os
import shutil
import sys
import tempfile
from pathlib import Path

from prepend_meta import prepend_meta
from runtime_paths import project_root
from xiaohongshu_media import XiaohongshuError, discover_note, download_assets, parse_note_id


class XiaohongshuCliError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _profile_dir() -> Path:
    return Path(os.environ.get("XHS_SFETCH_CHROME_PROFILE", project_root() / ".chrome-xiaohongshu"))


def run(url: str, output_dir: Path, overwrite: bool = False) -> Path:
    parse_note_id(url)
    output_dir.mkdir(parents=True, exist_ok=True)
    final_dir = None

    try:
        with tempfile.TemporaryDirectory(prefix="xhs-", dir=str(output_dir)) as temp_dir:
            work_dir = Path(temp_dir) / "note"
            try:
                metadata, video_url, image_urls = discover_note(url, _profile_dir())
                note_id = str(metadata.get("note_id") or parse_note_id(url))
                final_dir = output_dir / note_id
                if final_dir.exists() and not overwrite:
                    raise XiaohongshuCliError("OUTPUT_EXISTS", f"输出目录已存在: {final_dir}")
                downloaded = download_assets(metadata, video_url, image_urls, work_dir)
            except XiaohongshuCliError:
                raise
            except XiaohongshuError as exc:
                raise XiaohongshuCliError(exc.code, str(exc)) from exc
            except Exception as exc:
                raise XiaohongshuCliError("ASSET_DOWNLOAD_FAILED", str(exc)) from exc

            metadata = {**metadata, **downloaded, "assets": downloaded["asset_files"]}
            body_path = work_dir / f"{note_id}.md"
            body_path.write_text("", encoding="utf-8")
            if not prepend_meta(metadata, body_path):
                raise XiaohongshuCliError("METADATA_PARTIAL", "写入 frontmatter 失败")
            if final_dir and final_dir.exists():
                shutil.rmtree(final_dir)
            work_dir.replace(final_dir)
    except XiaohongshuCliError:
        raise
    except Exception as exc:
        raise XiaohongshuCliError("OUTPUT_FAILED", str(exc)) from exc
    return final_dir / f"{final_dir.name}.md"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="下载一个小红书视频或图文笔记")
    parser.add_argument("--url", required=True)
    parser.add_argument("--output", "-o", type=Path, default=project_root() / "10_raw" / "03_小红书")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)
    try:
        path = run(args.url, args.output, args.overwrite)
    except XiaohongshuCliError as exc:
        print(f"[{exc.code}] {exc}", file=sys.stderr)
        return 1
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
