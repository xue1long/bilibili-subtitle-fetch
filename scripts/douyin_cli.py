"""Download and transcribe one Douyin video."""

import argparse
import os
import shutil
import sys
import tempfile
from pathlib import Path

from douyin_media import DouyinMediaError, download_video, parse_video_id
from prepend_meta import prepend_meta
from runtime_paths import project_root
from transcribe_video import VideoTranscriptionError, transcribe_video


class DouyinCliError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _profile_dir() -> Path:
    return Path(os.environ.get("DOUYIN_SFETCH_CHROME_PROFILE", project_root() / ".chrome-douyin"))


def run(url: str, output_dir: Path, model_size: str = "small", keep_video: bool = False) -> Path:
    video_id = parse_video_id(url)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"DY{video_id}.md"
    try:
        with tempfile.TemporaryDirectory(prefix=f"douyin-{video_id}-", dir=str(output_dir.parent)) as temp_dir:
            temp = Path(temp_dir)
            video_path = temp / f"DY{video_id}.mp4"
            try:
                metadata = download_video(url, video_path, _profile_dir())
            except DouyinMediaError as exc:
                raise DouyinCliError(exc.code, str(exc)) from exc
            except Exception as exc:
                raise DouyinCliError("VIDEO_DOWNLOAD_FAILED", str(exc)) from exc

            try:
                srt = transcribe_video(video_path, model_size=model_size)
            except VideoTranscriptionError as exc:
                raise DouyinCliError(exc.code, str(exc)) from exc

            metadata = {**metadata, "platform": "douyin", "video_id": str(metadata.get("video_id", video_id))}
            metadata.setdefault("url", f"https://www.douyin.com/video/{video_id}")
            metadata["transcript_model"] = model_size
            body_path = temp / f"DY{video_id}.md"
            body_path.write_text(srt, encoding="utf-8")
            if not prepend_meta(metadata, body_path):
                raise DouyinCliError("METADATA_PARTIAL", "写入 frontmatter 失败")
            body_path.replace(output_path)
            if keep_video:
                shutil.copy2(video_path, output_dir / f"DY{video_id}.mp4")
    except DouyinCliError:
        output_path.unlink(missing_ok=True)
        raise
    except Exception as exc:
        output_path.unlink(missing_ok=True)
        raise DouyinCliError("VIDEO_DOWNLOAD_FAILED", str(exc)) from exc
    return output_path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="下载并转录一个抖音视频")
    parser.add_argument("--url", required=True)
    parser.add_argument("--output", "-o", type=Path, default=project_root() / "10_raw" / "02_抖音视频转录")
    parser.add_argument("--model", default="small")
    parser.add_argument("--keep-video", action="store_true")
    args = parser.parse_args(argv)
    try:
        path = run(args.url, args.output, args.model, args.keep_video)
    except DouyinCliError as exc:
        print(f"[{exc.code}] {exc}", file=sys.stderr)
        return 1
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
