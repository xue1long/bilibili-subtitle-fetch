"""Download a Xiaohongshu note and write its metadata manifest."""

import argparse
import os
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from prepend_meta import prepend_meta
from runtime_paths import project_root, raw_dir, scratch_dir
from transcribe_video import VideoTranscriptionError
from video_artifact import build_video_artifact
from xiaohongshu_media import (
    XiaohongshuError,
    discover_note,
    download_assets,
    download_video_asset,
    parse_note_id,
)


PLATFORM = "xiaohongshu"


@dataclass(frozen=True)
class _XiaohongshuRecord:
    video_id: str
    title: str = ""
    uploader: str = ""
    source_type: str = ""
    source_url: str = ""


class XiaohongshuCliError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _profile_dir() -> Path:
    return Path(os.environ.get("XHS_SFETCH_CHROME_PROFILE", project_root() / ".chrome-xiaohongshu"))


def _note_body(note_type: str | None, asset_files: list[str]) -> str:
    """正文写资源引用，让 Obsidian 等阅读器能直接看到图。"""
    if not asset_files:
        return ""
    return "\n\n".join(f"![图 {index}]({name})" for index, name in enumerate(asset_files, 1)) + "\n"


def _register_success(
    url: str,
    note_id: str,
    metadata: dict,
    output_dir: Path,
    *,
    status: str = "assets_downloaded",
    source: str = "native",
    root: Path | None = None,
) -> None:
    """Best-effort record keeping: storage failures must not break the main flow.

    ``root`` defaults to ``project_root()`` and may be overridden by tests to keep
    storage side-effects inside a temporary directory.
    """
    try:
        from storage.platform_manifest import manifest_path, mark_completed, upsert
        from storage.sqlite_store import SQLiteStore
    except Exception:
        return

    root = root or project_root()
    record_metadata = {
        "video_id": note_id,
        "note_id": note_id,
        "title": metadata.get("title", ""),
        "uploader": metadata.get("uploader", ""),
        "description": metadata.get("description", ""),
        "url": metadata.get("url") or f"https://www.xiaohongshu.com/explore/{note_id}",
        "source_type": "xiaohongshu_cli",
        "source_url": url,
        "note_type": metadata.get("note_type", ""),
        "asset_count": metadata.get("asset_count", 0),
        "assets": metadata.get("assets", []),
    }
    try:
        upsert(manifest_path(root), PLATFORM, [record_metadata])
        mark_completed(
            manifest_path(root),
            PLATFORM,
            note_id,
            status=status,
            output_path=str(output_dir),
        )
    except Exception:
        return

    try:
        store = SQLiteStore(root / "data" / "subtitle_tasks.db")
        store.upsert_videos(
            [_XiaohongshuRecord(
                video_id=note_id,
                title=metadata.get("title", ""),
                uploader=metadata.get("uploader", ""),
                source_type="xiaohongshu_cli",
                source_url=url,
            )],
            platform=PLATFORM,
        )
        store.update_job((PLATFORM, note_id), status, source=source)
    except Exception:
        return


def _transcribe_video_note(url: str, note: dict, video_url: str, model_size: str, work_dir: Path) -> tuple[str, dict]:
    """视频笔记：视频落本地暂存区 → 转录（无语音则抽帧）→ 删除视频，产物根只留文本与图片。"""
    scratch = scratch_dir()
    video_path = download_video_asset(video_url, note, scratch)
    try:
        return build_video_artifact(
            video_path,
            frames_dir=work_dir / "images",
            md_dir=work_dir,
            model_size=model_size,
        )
    except VideoTranscriptionError as exc:
        raise XiaohongshuCliError(exc.code, str(exc)) from exc
    finally:
        video_path.unlink(missing_ok=True)


def run(
    url: str,
    output_dir: Path,
    overwrite: bool = False,
    model_size: str = "small",
    *,
    state_root: Path | None = None,
) -> Path:
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
                    # 产物已在，状态库可能还是空的；先补登记再报错。
                    _register_success(url, note_id, metadata, final_dir, root=state_root)
                    raise XiaohongshuCliError("OUTPUT_EXISTS", f"输出目录已存在: {final_dir}")
                if video_url:
                    body, extra = _transcribe_video_note(url, metadata, video_url, model_size, work_dir)
                    downloaded = {"asset_count": 0, "asset_files": []}
                    metadata.update(extra)
                    if extra.get("frame_count"):
                        metadata["assets"] = [f"images/{name}" for name in sorted(p.name for p in (work_dir / "images").glob("*.webp"))]
                        metadata["asset_count"] = extra["frame_count"]
                else:
                    downloaded = download_assets(metadata, image_urls, work_dir)
                    body = _note_body(metadata.get("note_type"), downloaded["asset_files"])
                work_dir.mkdir(parents=True, exist_ok=True)
            except XiaohongshuCliError:
                raise
            except XiaohongshuError as exc:
                raise XiaohongshuCliError(exc.code, str(exc)) from exc
            except Exception as exc:
                raise XiaohongshuCliError("ASSET_DOWNLOAD_FAILED", str(exc)) from exc

            if downloaded["asset_files"]:
                metadata = {**metadata, **downloaded, "assets": downloaded["asset_files"]}
            body_path = work_dir / f"{note_id}.md"
            body_path.write_text(body, encoding="utf-8")
            if not prepend_meta(metadata, body_path):
                raise XiaohongshuCliError("METADATA_PARTIAL", "写入 frontmatter 失败")
            if final_dir and final_dir.exists():
                shutil.rmtree(final_dir)
            # 暂存区与产物根可能不在同一个盘，os.replace 跨盘会失败
            shutil.move(str(work_dir), str(final_dir))
            if video_url:
                _register_success(url, note_id, metadata, final_dir, status="asr_success", source="asr", root=state_root)
            else:
                _register_success(url, note_id, metadata, final_dir, root=state_root)
    except XiaohongshuCliError:
        raise
    except Exception as exc:
        raise XiaohongshuCliError("OUTPUT_FAILED", str(exc)) from exc
    return final_dir / f"{final_dir.name}.md"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="下载一个小红书视频或图文笔记")
    parser.add_argument("--url", required=True)
    parser.add_argument("--output", "-o", type=Path, default=raw_dir() / "03_小红书")
    parser.add_argument("--model", default="small")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)
    try:
        path = run(args.url, args.output, args.overwrite, args.model)
    except XiaohongshuCliError as exc:
        print(f"[{exc.code}] {exc}", file=sys.stderr)
        return 1
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
