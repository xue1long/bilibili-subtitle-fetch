"""Transcribe a local video file to SRT with ffmpeg and faster-whisper."""

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

from subtitle_rescue import transcribe


class VideoTranscriptionError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def transcribe_video(video_path: Path, model_size: str = "small") -> str:
    video_path = Path(video_path)
    if not video_path.is_file():
        raise VideoTranscriptionError("VIDEO_NOT_FOUND", f"视频文件不存在: {video_path}")

    with tempfile.TemporaryDirectory(prefix="video-transcription-") as temp_dir:
        audio_path = Path(temp_dir) / "audio.mp3"
        try:
            result = subprocess.run(
                ["ffmpeg", "-y", "-i", str(video_path), "-vn", "-acodec", "libmp3lame", "-ab", "64k", str(audio_path)],
                capture_output=True,
            )
        except OSError as exc:
            raise VideoTranscriptionError("AUDIO_EXTRACT_FAILED", str(exc)) from exc
        if result.returncode:
            detail = result.stderr.decode(errors="ignore")[-500:]
            raise VideoTranscriptionError("AUDIO_EXTRACT_FAILED", detail or "ffmpeg 提取音频失败")
        try:
            return transcribe(audio_path, model_size=model_size)
        except Exception as exc:
            raise VideoTranscriptionError("ASR_FAILED", str(exc)) from exc


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="从本地视频提取音频并转录为 SRT")
    parser.add_argument("video", type=Path, help="本地视频文件路径")
    parser.add_argument("--output", "-o", type=Path, help="SRT 输出路径，默认与视频同名")
    parser.add_argument("--model", default="small", help="faster-whisper 模型，默认 small")
    parser.add_argument("--overwrite", action="store_true", help="允许覆盖已有 SRT 文件")
    args = parser.parse_args(argv)

    output_path = args.output or args.video.with_suffix(".srt")
    if output_path.exists() and not args.overwrite:
        print(f"[OUTPUT_EXISTS] 文件已存在；如需覆盖请加 --overwrite: {output_path}", file=sys.stderr)
        return 1

    try:
        srt = transcribe_video(args.video, model_size=args.model)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(srt, encoding="utf-8")
    except VideoTranscriptionError as exc:
        print(f"[{exc.code}] {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"[OUTPUT_FAILED] {exc}", file=sys.stderr)
        return 1

    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
