"""Download and transcribe one Douyin video."""

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from douyin_media import DouyinMediaError, download_video, parse_video_id
from prepend_meta import prepend_meta
from runtime_paths import project_root
from subtitle_rescue import transcribe


class DouyinCliError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _profile_dir() -> Path:
    return Path(os.environ.get("DOUYIN_SFETCH_CHROME_PROFILE", project_root() / ".chrome-douyin"))


def _extract_audio(video_path: Path, audio_path: Path) -> None:
    result = subprocess.run(
        ["ffmpeg", "-y", "-i", str(video_path), "-vn", "-acodec", "libmp3lame", "-ab", "64k", str(audio_path)],
        capture_output=True,
    )
    if result.returncode:
        detail = result.stderr.decode(errors="ignore")[-500:]
        raise DouyinCliError("AUDIO_EXTRACT_FAILED", detail or "ffmpeg 提取音频失败")


def run(url: str, output_dir: Path, model_size: str = "small", keep_video: bool = False) -> Path:
    video_id = parse_video_id(url)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"DY{video_id}.md"
    try:
        with tempfile.TemporaryDirectory(prefix=f"douyin-{video_id}-", dir=str(output_dir.parent)) as temp_dir:
            temp = Path(temp_dir)
            video_path = temp / f"DY{video_id}.mp4"
            audio_path = temp / f"DY{video_id}.mp3"
            try:
                metadata = download_video(url, video_path, _profile_dir())
            except DouyinMediaError as exc:
                raise DouyinCliError(exc.code, str(exc)) from exc
            except Exception as exc:
                raise DouyinCliError("VIDEO_DOWNLOAD_FAILED", str(exc)) from exc

            _extract_audio(video_path, audio_path)
            try:
                srt = transcribe(audio_path, model_size=model_size)
            except Exception as exc:
                raise DouyinCliError("ASR_FAILED", str(exc)) from exc

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
