"""视频产物的正文组装：有语音走转录，无语音走关键帧。

视频是中间产物，不进产物根。但「转录成功才删除」这个前提对无语音视频不成立 ——
转录不出文字时视频一删，内容就彻底消失了。所以无语音的视频改为**抽取关键帧存图**：
帧是可以进产物根的，内容因此不丢，形态与图文笔记一致。
"""

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from transcribe_video import transcribe_video


# 部分环境的 ffprobe 实为 ffmpeg 副本（PATH 上被同名文件遮蔽），
# 不认识 -show_entries / -select_streams；此时改用 ffmpeg -i 的输出来探测。
_FFPROBE_UNSUPPORTED = ("Unrecognized option", "Option not found")
_DURATION_PATTERN = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")
_AUDIO_STREAM_PATTERN = re.compile(r"Stream #\d+:\d+.*: Audio:", re.MULTILINE)
_ANY_STREAM_PATTERN = re.compile(r"Stream #\d+:\d+", re.MULTILINE)


def _ffprobe_unavailable(stderr: str) -> bool:
    """ffprobe 是否因为不认识选项而失败（而不是文件本身有问题）。"""
    return any(marker in stderr for marker in _FFPROBE_UNSUPPORTED)


def _ffmpeg_probe_text(video_path: Path) -> str:
    """用 ffmpeg -i 读容器信息：ffprobe 不支持 -show_* 时的回退路径。"""
    result = subprocess.run(["ffmpeg", "-i", str(video_path)], capture_output=True)
    return result.stderr.decode(errors="ignore")


def _parse_duration(probe_text: str) -> float:
    match = _DURATION_PATTERN.search(probe_text)
    if not match:
        return 0.0
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def _parse_has_audio_stream(probe_text: str) -> bool:
    if _AUDIO_STREAM_PATTERN.search(probe_text):
        return True
    if _ANY_STREAM_PATTERN.search(probe_text):
        return False
    # 读不出任何流说明文件不是有效视频，不能当成「无音频轨」
    raise RuntimeError("视频探测失败: ffmpeg 无法读取该文件")


def plain_text(srt: str) -> str:
    """从 SRT 里取出纯文本（去掉序号与时间轴行）。"""
    words = []
    for line in srt.splitlines():
        text = line.strip()
        if not text or text.isdigit() or "-->" in text:
            continue
        words.append(text)
    return " ".join(words).strip()


def probe_duration(video_path: Path) -> float:
    """视频时长（秒）；探测失败返回 0。"""
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(video_path)],
        capture_output=True,
    )
    if not result.returncode:
        try:
            return float(result.stdout.decode(errors="ignore").strip().split(",")[0])
        except (ValueError, IndexError):
            return 0.0
    stderr = result.stderr.decode(errors="ignore")
    if _ffprobe_unavailable(stderr):
        return _parse_duration(_ffmpeg_probe_text(video_path))
    return 0.0


def has_audio_stream(video_path: Path) -> bool:
    """是否含音频轨。

    探测失败（文件不是有效视频）要报错，不能当成「无音频轨」——
    否则真正的坏文件会被静默走到抽帧分支，报出误导性的错误。
    """
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a",
         "-show_entries", "stream=index", "-of", "csv=p=0", str(video_path)],
        capture_output=True,
    )
    if not result.returncode:
        return bool(result.stdout.decode(errors="ignore").strip())
    stderr = result.stderr.decode(errors="ignore")
    if _ffprobe_unavailable(stderr):
        return _parse_has_audio_stream(_ffmpeg_probe_text(video_path))
    detail = stderr[-200:]
    raise RuntimeError(f"视频探测失败: {detail or 'ffprobe 无法读取该文件'}")


def extract_key_frames(
    video_path: Path,
    output_dir: Path,
    interval: float = 2.0,
    max_frames: int = 24,
    width: int = 540,
) -> list[Path]:
    """抽取关键帧存成 webp，返回文件列表。

    帧间隔会自动放大到能覆盖整段视频（上限 max_frames 帧），
    否则长视频只会留下开头一段。
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    duration = probe_duration(video_path)
    if duration > 0:
        interval = max(interval, duration / max_frames)
    result = subprocess.run(
        [
            "ffmpeg", "-y", "-i", str(video_path),
            # 只在比目标宽时缩小，避免小视频被放大
            "-vf", f"fps=1/{interval:.3f},scale=w=min({width}\\,iw):h=-2",
            "-frames:v", str(max_frames),
            # 必须显式指定静态 webp 编码器：按扩展名 ffmpeg 会选 libwebp_anim，
            # 把整段动画写成一个文件，只剩一帧
            "-c:v", "libwebp", "-quality", "80",
            str(output_dir / "%02d.webp"),
        ],
        capture_output=True,
    )
    frames = sorted(output_dir.glob("*.webp"))
    if not frames:
        detail = result.stderr.decode(errors="ignore")[-300:]
        raise RuntimeError(f"关键帧抽取失败: {detail or 'ffmpeg 无输出'}")
    return frames


def build_video_artifact(
    video_path: Path,
    frames_dir: Path,
    md_dir: Path,
    model_size: str = "small",
    interval: float = 2.0,
    max_frames: int = 24,
) -> tuple[str, dict]:
    """产出视频的正文与附加元数据。视频文件由调用方负责删除。

    有语音 → 正文是 SRT 转录文本。
    无语音 → 抽取关键帧，正文是帧图引用。

    没有音频轨的视频不能直接进 ASR：ffmpeg 抽音频那一步就会失败，
    必须在进 ASR 之前就分流到关键帧。
    """
    if has_audio_stream(video_path):
        srt = transcribe_video(video_path, model_size=model_size)
        if plain_text(srt):
            return srt, {"transcript_model": model_size}
        reason = "有音频轨但未识别出内容"
    else:
        reason = "无音频轨"

    frames = extract_key_frames(video_path, frames_dir, interval=interval, max_frames=max_frames)
    refs = []
    for index, frame in enumerate(frames, 1):
        relative = os.path.relpath(frame, md_dir).replace("\\", "/")
        refs.append((index, relative))
    body = (
        f"> 该视频未识别到语音（{reason}），已抽取 {len(frames)} 张关键帧。\n\n"
        + "\n\n".join(f"![帧 {index}]({relative})" for index, relative in refs)
        + "\n"
    )
    return body, {"frame_count": len(frames), "frame_interval": interval, "speech": "none"}


# ---------------------------------------------------------------------------
# CLI：把**本地已有**的视频变成合规产物
#
# 下载型的入口（douyin_cli / xiaohongshu_cli）从 URL 起步，本命令从本地视频
# 起步 —— 用于补跑那些「视频已下载但当时没转成」的存量。
# ---------------------------------------------------------------------------


def artifact_paths(output_dir: Path, note_id: str, flat: bool) -> tuple[Path, Path]:
    """返回 (markdown 路径, 关键帧目录)。

    目录式（默认，与当前代码产出一致）::

        <output_dir>/<id>/<id>.md
        <output_dir>/<id>/images/*.webp

    扁平式（``--flat``，用于补跑旧格式存量）::

        <output_dir>/<id>.md
        <output_dir>/images/<id>/*.webp
    """
    if flat:
        return output_dir / f"{note_id}.md", output_dir / "images" / note_id
    return output_dir / note_id / f"{note_id}.md", output_dir / note_id / "images"


def _load_manifest(path: Path) -> dict:
    """读取 platform manifest，按 video_id 索引。读不到就返回空表。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    sources = data.get("sources")
    if not isinstance(sources, dict):
        return {}
    return sources


def _metadata_for(note_id: str, args, extra: dict, manifest: dict) -> dict:
    """组装 frontmatter 元数据。

    优先级：``--metadata`` 显式给的 > manifest 里登记的 > CLI 参数推导的。
    """
    meta: dict = {}
    record = manifest.get(args.platform, {}).get(note_id)
    if isinstance(record, dict):
        meta.update({k: v for k, v in record.items() if k not in ("video_id", "note_id")})
    meta.update(args.metadata or {})

    if args.platform == "xiaohongshu":
        meta["note_id"] = note_id
    else:
        meta["video_id"] = note_id
    meta.setdefault("platform", args.platform)
    if not meta.get("url") and args.source_url:
        meta["url"] = args.source_url
    if extra.get("transcript_model"):
        meta["transcript_model"] = extra["transcript_model"]
    if extra.get("frame_count"):
        meta["asset_count"] = extra["frame_count"]
    if extra.get("assets"):
        meta["assets"] = extra["assets"]
    return meta


def _asset_paths(frames_dir: Path, md_path: Path) -> list[str]:
    """关键帧相对 markdown 所在目录的路径列表。"""
    base = md_path.parent
    return [
        os.path.relpath(frame, base).replace("\\", "/")
        for frame in sorted(frames_dir.glob("*.webp"))
    ]


def build_one(video_path: Path, output_dir: Path, args, manifest: dict) -> Path:
    """处理一个本地视频，返回产出的 markdown 路径。"""
    note_id = video_path.stem
    md_path, frames_dir = artifact_paths(output_dir, note_id, args.flat)
    md_path.parent.mkdir(parents=True, exist_ok=True)

    body, extra = build_video_artifact(
        video_path,
        frames_dir=frames_dir,
        md_dir=md_path.parent,
        model_size=args.model,
    )
    if extra.get("frame_count"):
        extra["assets"] = _asset_paths(frames_dir, md_path)

    md_path.write_text(body, encoding="utf-8")
    from prepend_meta import prepend_meta

    metadata = _metadata_for(note_id, args, extra, manifest)
    if not prepend_meta(metadata, md_path):
        raise RuntimeError(f"写入 frontmatter 失败: {md_path}")

    if not args.keep_video:
        video_path.unlink(missing_ok=True)
    return md_path


def collect_videos(args) -> list[Path]:
    """待处理的视频列表：``--video`` 显式给的 + ``--from-dir`` 扫到的。"""
    videos = [Path(v) for v in (args.video or [])]
    if args.from_dir:
        directory = Path(args.from_dir)
        videos.extend(sorted(directory.glob("*.mp4")))
    return videos


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="把本地已有的视频转成产物：有语音走 ASR，无语音抽关键帧")
    parser.add_argument("--video", action="append",
                        help="本地视频路径，可重复；产物 ID 取文件名")
    parser.add_argument("--from-dir", type=Path,
                        help="扫描该目录下的 *.mp4 批量处理")
    parser.add_argument("--output-dir", "-o", type=Path, required=True,
                        help="产物目录")
    parser.add_argument("--platform", default="douyin",
                        help="frontmatter 的 platform 字段，默认 douyin")
    parser.add_argument("--model", default="small", help="ASR 模型，默认 small")
    parser.add_argument("--metadata", type=json.loads,
                        help="额外元数据 JSON，覆盖 manifest 里的同名字段")
    parser.add_argument("--manifest", type=Path,
                        help="platform manifest 路径，用于补齐 title/uploader 等")
    parser.add_argument("--source-url", help="产物 frontmatter 里的 url")
    parser.add_argument("--flat", action="store_true",
                        help="扁平布局（<id>.md 平铺），用于补跑旧格式存量")
    parser.add_argument("--keep-video", action="store_true",
                        help="保留源视频（默认按「视频是中间产物」删除）")
    parser.add_argument("--overwrite", action="store_true",
                        help="覆盖已有产物；默认已有产物则跳过")
    parser.add_argument("--dry-run", action="store_true", help="只报告，不改动")
    args = parser.parse_args(argv)

    videos = collect_videos(args)
    if not videos:
        print("没有待处理的视频（用 --video 或 --from-dir 指定）", file=sys.stderr)
        return 2

    manifest = _load_manifest(args.manifest) if args.manifest else {}
    done = skipped = failed = 0
    for video in videos:
        if not video.is_file() or video.stat().st_size == 0:
            print(f"[skip] {video.name}: 文件不存在或为空")
            skipped += 1
            continue
        md_path, _ = artifact_paths(args.output_dir, video.stem, args.flat)
        if md_path.exists() and not args.overwrite:
            print(f"[skip] {video.name}: 产物已存在（{md_path.name}）")
            skipped += 1
            continue
        if args.dry_run:
            print(f"[dry-run] {video.name} -> {md_path}")
            continue
        try:
            produced = build_one(video, args.output_dir, args, manifest)
        except Exception as exc:
            failed += 1
            print(f"[fail] {video.name}: {exc}", file=sys.stderr)
            continue
        done += 1
        print(f"[ok] {video.name} -> {produced}")

    print(f"完成 {done} 个，跳过 {skipped} 个，失败 {failed} 个")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
