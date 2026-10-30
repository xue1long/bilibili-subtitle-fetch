"""Download one Douyin post: video with transcription, or image post with images."""

import argparse
import os
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from douyin_media import (
    DouyinMediaError,
    download_images,
    download_video,
    extract_page_metadata,
    parse_video_id,
    resolve_post,
)
from prepend_meta import prepend_meta
from runtime_paths import project_root, raw_dir, scratch_dir
from transcribe_video import VideoTranscriptionError
from video_artifact import build_video_artifact


PLATFORM = "douyin"


@dataclass(frozen=True)
class _DouyinRecord:
    video_id: str
    title: str = ""
    uploader: str = ""
    source_type: str = ""
    source_url: str = ""


class DouyinCliError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _profile_dir() -> Path:
    return Path(os.environ.get("DOUYIN_SFETCH_CHROME_PROFILE", project_root() / ".chrome-douyin"))


def _image_body(files: list[str]) -> str:
    """正文写 markdown 图片引用，让 Obsidian 等阅读器能直接看到图。"""
    return "\n\n".join(f"![图 {index}]({name})" for index, name in enumerate(files, 1)) + "\n"


def _register_success(
    url: str,
    video_id: str,
    metadata: dict,
    output_path: Path,
    *,
    status: str = "asr_success",
    source: str = "asr",
    root: Path | None = None,
) -> None:
    """Best-effort record keeping: ignore storage failures so the main flow is unaffected.

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
        "video_id": video_id,
        "title": metadata.get("title", ""),
        "uploader": metadata.get("uploader", ""),
        "description": metadata.get("description", ""),
        "url": metadata.get("url") or f"https://www.douyin.com/video/{video_id}",
        "source_type": "douyin_cli",
        "source_url": url,
        "transcript_model": metadata.get("transcript_model", ""),
    }
    try:
        upsert(manifest_path(root), PLATFORM, [record_metadata])
        mark_completed(
            manifest_path(root),
            PLATFORM,
            video_id,
            status=status,
            output_path=str(output_path),
        )
    except Exception:
        return

    try:
        store = SQLiteStore(root / "data" / "subtitle_tasks.db")
        store.upsert_videos(
            [_DouyinRecord(
                video_id=video_id,
                title=metadata.get("title", ""),
                uploader=metadata.get("uploader", ""),
                source_type="douyin_cli",
                source_url=url,
            )],
            platform=PLATFORM,
        )
        store.update_job((PLATFORM, video_id), status, source=source)
    except Exception:
        return


def _run_image_post(
    post: dict,
    source_url: str,
    output_dir: Path,
    *,
    skip_if_completed: bool = True,
    state_root: Path | None = None,
) -> Path:
    """图文帖：下载全部图片，写元数据清单，不做语音转录。"""
    video_id = post["video_id"]
    output_dir.mkdir(parents=True, exist_ok=True)
    final_dir = output_dir / f"DY{video_id}"
    body_path = final_dir / f"DY{video_id}.md"
    if skip_if_completed and body_path.exists():
        return body_path
    if final_dir.exists():
        shutil.rmtree(final_dir)
    try:
        files = download_images(post.get("image_urls") or [], final_dir)
    except DouyinMediaError as exc:
        shutil.rmtree(final_dir, ignore_errors=True)
        raise DouyinCliError(exc.code, str(exc)) from exc
    except Exception as exc:
        shutil.rmtree(final_dir, ignore_errors=True)
        raise DouyinCliError("ASSET_DOWNLOAD_FAILED", str(exc)) from exc

    metadata = extract_page_metadata(post.get("payload") or {}, source_url)
    metadata.update({"note_type": "image", "asset_count": len(files), "assets": files})
    body_path.write_text(_image_body(files), encoding="utf-8")
    if not prepend_meta(metadata, body_path):
        raise DouyinCliError("METADATA_PARTIAL", "写入 frontmatter 失败")
    _register_success(
        source_url,
        video_id,
        metadata,
        body_path,
        status="assets_downloaded",
        source="native",
        root=state_root,
    )
    return body_path


def _run_video_post(
    url: str,
    post: dict | None,
    output_dir: Path,
    model_size: str,
    keep_video: bool,
    *,
    skip_if_completed: bool = True,
    state_root: Path | None = None,
) -> Path:
    video_id = parse_video_id(url)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"DY{video_id}.md"
    if skip_if_completed and output_path.exists():
        return output_path
    try:
        # 视频落在项目本地的媒体暂存区，不进产物根 —— 产物根可能在同步盘上，
        # 媒体进去会被完整上传一遍再删除。
        scratch = scratch_dir()
        scratch.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=f"douyin-{video_id}-", dir=str(scratch)) as temp_dir:
            temp = Path(temp_dir)
            video_path = temp / f"DY{video_id}.mp4"
            try:
                metadata = download_video(url, video_path, _profile_dir(), post=post)
            except DouyinMediaError as exc:
                raise DouyinCliError(exc.code, str(exc)) from exc
            except Exception as exc:
                raise DouyinCliError("VIDEO_DOWNLOAD_FAILED", str(exc)) from exc

            try:
                body, extra = build_video_artifact(
                    video_path,
                    frames_dir=output_dir / "images" / f"DY{video_id}",
                    md_dir=output_dir,
                    model_size=model_size,
                )
            except VideoTranscriptionError as exc:
                raise DouyinCliError(exc.code, str(exc)) from exc
            except Exception as exc:
                raise DouyinCliError("VIDEO_TRANSCRIPTION_FAILED", str(exc)) from exc

            metadata = {**metadata, "platform": "douyin", "video_id": str(metadata.get("video_id", video_id))}
            metadata.setdefault("url", f"https://www.douyin.com/video/{video_id}")
            metadata.update(extra)
            if extra.get("frame_count"):
                frames_dir = output_dir / "images" / f"DY{video_id}"
                metadata["assets"] = [
                    f"images/DY{video_id}/{path.name}" for path in sorted(frames_dir.glob("*.webp"))
                ]
                metadata["asset_count"] = extra["frame_count"]
            body_path = temp / f"DY{video_id}.md"
            body_path.write_text(body, encoding="utf-8")
            if not prepend_meta(metadata, body_path):
                raise DouyinCliError("METADATA_PARTIAL", "写入 frontmatter 失败")
            # 暂存区与产物根可能不在同一个盘，os.replace 跨盘会失败
            shutil.move(str(body_path), str(output_path))
            if keep_video:
                kept_dir = scratch_dir() / "kept"
                kept_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(video_path, kept_dir / f"DY{video_id}.mp4")
    except DouyinCliError:
        output_path.unlink(missing_ok=True)
        shutil.rmtree(output_dir / "images" / f"DY{video_id}", ignore_errors=True)
        raise
    except Exception as exc:
        output_path.unlink(missing_ok=True)
        shutil.rmtree(output_dir / "images" / f"DY{video_id}", ignore_errors=True)
        raise DouyinCliError("VIDEO_DOWNLOAD_FAILED", str(exc)) from exc
    _register_success(url, video_id, metadata, output_path, root=state_root)
    return output_path


def run(
    url: str,
    output_dir: Path,
    model_size: str = "small",
    keep_video: bool = False,
    image_output_dir: Path | None = None,
    skip_if_completed: bool = True,
    *,
    state_root: Path | None = None,
) -> Path:
    post = resolve_post(url, _profile_dir())
    if post and post.get("media_kind") == "image":
        images_dir = image_output_dir or output_dir.parent / "05_抖音笔记图片"
        return _run_image_post(
            post,
            url,
            images_dir,
            skip_if_completed=skip_if_completed,
            state_root=state_root,
        )
    return _run_video_post(
        url,
        post,
        output_dir,
        model_size,
        keep_video,
        skip_if_completed=skip_if_completed,
        state_root=state_root,
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="下载并转录一个抖音视频，或下载一个抖音图文帖")
    parser.add_argument("--url", required=True)
    parser.add_argument("--output", "-o", type=Path, default=raw_dir() / "02_抖音视频转录")
    parser.add_argument("--image-output", type=Path, default=raw_dir() / "05_抖音笔记图片")
    parser.add_argument("--model", default="small")
    parser.add_argument("--keep-video", action="store_true")
    parser.add_argument("--overwrite", action="store_true",
                        help="强制重新下载；默认已存在 .md 时跳过")
    args = parser.parse_args(argv)
    try:
        path = run(
            args.url,
            args.output,
            args.model,
            args.keep_video,
            args.image_output,
            skip_if_completed=not args.overwrite,
        )
    except DouyinCliError as exc:
        print(f"[{exc.code}] {exc}", file=sys.stderr)
        return 1
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
