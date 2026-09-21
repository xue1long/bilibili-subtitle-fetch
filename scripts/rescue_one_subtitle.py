#!/usr/bin/env python3
import argparse
import asyncio
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Optional, Tuple

from runtime_paths import chrome_profile_dir, default_output_dir, edge_profile_dir
from bili_guard import (
    CircuitBreaker,
    FailureKind,
    RateLimiter,
    check_login_state,
    classify_failure,
    download_list_lock,
)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

M4S_HEADERS = {
    "Referer": "https://www.bilibili.com",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
}
DEFAULT_BROWSER = "chrome"


def project_root() -> Path:
    from runtime_paths import project_root as get_project_root
    return get_project_root()


def format_timestamp(seconds: float) -> str:
    total_millis = int(round(seconds * 1000))
    hours, remainder = divmod(total_millis, 3600 * 1000)
    minutes, remainder = divmod(remainder, 60 * 1000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def format_srt(segments) -> str:
    lines = []
    for i, seg in enumerate(segments, 1):
        lines.append(str(i))
        lines.append(f"{format_timestamp(seg.start)} --> {format_timestamp(seg.end)}")
        lines.append(seg.text.strip())
        lines.append("")
    return "\n".join(lines)


FETCH_DASH_JS = """
async () => {
    const s = window.__INITIAL_STATE__;
    if (!s || !s.videoData) return {error: "no INITIAL_STATE or videoData"};
    const v = s.videoData;
    const meta = {
        bv: s.bvid,
        bvid: s.bvid,
        cid: s.cid,
        title: v.title,
        duration: v.duration,
        uploader: v.owner && v.owner.name,
        view_count: v.stat && v.stat.view,
        like_count: v.stat && v.stat.like,
        description: v.desc,
        video_published_at: v.pubdate ? new Date(v.pubdate * 1000).toISOString().slice(0,10) : null,
    };

    const dashRes = await fetch(
        `https://api.bilibili.com/x/player/playurl?bvid=${meta.bvid}&cid=${meta.cid}&fnval=16&fnver=0&fourk=1&qn=80`,
        {credentials: 'include'}
    );
    const dash = await dashRes.json();
    if (dash.code !== 0) return {...meta, error: `DASH API code=${dash.code}: ${dash.message}`};

    const audios = dash.data && dash.data.dash && dash.data.dash.audio;
    const videos = dash.data && dash.data.dash && dash.data.dash.video;
    if (audios && audios.length > 0) {
        return {...meta, audioUrl: audios[0].baseUrl || audios[0].base_url};
    }
    if (videos && videos.length > 0) {
        return {...meta, audioUrl: videos[0].baseUrl || videos[0].base_url, fallback: "video"};
    }
    if (dash.data && dash.data.durl && dash.data.durl.length > 0) {
        return {...meta, audioUrl: dash.data.durl[0].url, fallback: "flv"};
    }
    return {...meta, error: "no audio/video URL in DASH"};
}
"""


def download_m4s(url: str, out_path: Path) -> None:
    req = urllib.request.Request(url, headers=M4S_HEADERS)
    with urllib.request.urlopen(req, timeout=60) as resp:
        out_path.write_bytes(resp.read())


async def fetch_audio(bvid: str, out_dir: Path, browser: str = DEFAULT_BROWSER) -> Tuple[Path, dict]:
    from playwright.async_api import async_playwright

    out_dir.mkdir(parents=True, exist_ok=True)
    url = f"https://www.bilibili.com/video/{bvid}"
    mp3_path = out_dir / f"{bvid}.mp3"

    async with async_playwright() as p:
        profile_dir = chrome_profile_dir() if browser == "chrome" else edge_profile_dir()
        browser_context = await p.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            executable_path=p.chromium.executable_path,
            headless=True,
            args=["--no-sandbox"],
        )
        page = await browser_context.new_page()
        await page.goto(url, wait_until="networkidle", timeout=60000)

        session = check_login_state(page)
        if not session.ok:
            await browser_context.close()
            raise RuntimeError(session.reason)

        result = await page.evaluate(FETCH_DASH_JS)
        await browser_context.close()

    if "error" in result:
        raise RuntimeError(f"DASH fetch failed: {result['error']}")

    print(f"  title: {result.get('title', '?')}")
    print(f"  uploader: {result.get('uploader', '?')}")
    print(f"  duration: {result.get('duration', '?')}s")

    audio_url = result["audioUrl"]
    is_video_fallback = result.get("fallback") == "video"

    if is_video_fallback:
        print(f"  [fallback] 无 audio stream，用 video stream（ffmpeg 直接读 URL）")
        proc = subprocess.run(
            [
                "ffmpeg", "-y",
                "-user_agent", M4S_HEADERS["User-Agent"],
                "-headers", f"Referer: {M4S_HEADERS['Referer']}\r\n",
                "-i", audio_url,
                "-vn", "-acodec", "libmp3lame", "-ab", "64k",
                str(mp3_path),
            ],
            capture_output=True,
        )
    else:
        m4s_path = out_dir / f"{bvid}.m4s"
        download_m4s(audio_url, m4s_path)
        proc = subprocess.run(
            [
                "ffmpeg", "-y", "-i", str(m4s_path),
                "-vn", "-acodec", "libmp3lame", "-ab", "64k",
                str(mp3_path),
            ],
            capture_output=True,
        )
        m4s_path.unlink()

    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {proc.stderr.decode(errors='ignore')[-500:]}")
    meta = {k: v for k, v in result.items() if k not in ("audioUrl", "fallback")}
    return mp3_path, meta


def transcribe(audio_path: Path, model_size: str = "large-v3", language: Optional[str] = None) -> str:
    from faster_whisper import WhisperModel

    print(f"  加载模型 {model_size}...")
    model = WhisperModel(model_size, device="auto", compute_type="auto")
    print(f"  开始转录（首段前可能有几秒等待，VAD 沉默检测）...")
    segments_iter, info = model.transcribe(str(audio_path), language=language, beam_size=5)
    print(f"  检测语言: {info.language} (prob={info.language_probability:.2f})")

    segments = []
    for seg in segments_iter:
        segments.append(seg)
        print(f"  [{format_timestamp(seg.start)}] {seg.text.strip()}")

    print(f"  共 {len(segments)} 段")
    return format_srt(segments)


def update_download_list(output_dir: Path, bvid: str, status: str,
                         subtitle_path: Optional[Path] = None,
                         error: Optional[str] = None,
                         error_code: Optional[str] = None,
                         retry_after: Optional[str] = None) -> None:
    list_path = output_dir / "download_list.json"
    try:
        data = json.loads(list_path.read_text(encoding="utf-8")) if list_path.exists() else {
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "records": {},
        }
    except (OSError, json.JSONDecodeError):
        data = {"created_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "records": {}}
    record = {
        "status": status,
        "downloaded_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    if subtitle_path:
        record["subtitle_path"] = str(subtitle_path)
    if error:
        record["error"] = error
    if error_code:
        record["error_code"] = error_code
    if retry_after:
        record["retry_after"] = retry_after
    output_dir.mkdir(parents=True, exist_ok=True)
    with download_list_lock():
        if list_path.exists():
            try:
                data = json.loads(list_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                pass
        data.setdefault("records", {})[bvid] = record
        data["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        list_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description="Rescue one failed subtitle via audio ASR (Playwright + browser profile)")
    parser.add_argument("bvid", help="BV 号，例如 BV1Xx411c7cH")
    parser.add_argument("--model", default="large-v3", help="faster-whisper 模型（tiny/base/small/medium/large-v3）")
    parser.add_argument("--keep-audio", action="store_true", help="保留临时音频文件")
    parser.add_argument("--browser", choices=("chrome", "edge"), default=DEFAULT_BROWSER)
    args = parser.parse_args()

    bvid = args.bvid.strip()
    if not bvid.startswith("BV") or len(bvid) != 12:
        print(f"错误: BV 号格式不正确（应为 BV + 10 位字符）: {bvid}", file=sys.stderr)
        sys.exit(1)

    root = project_root()
    audio_dir = root / "tmp" / "audio"
    output_dir = default_output_dir()
    breaker = CircuitBreaker(output_dir / ".bili_guard_state.json")
    if not breaker.allow():
        retry_after = breaker.retry_after
        retry_after = retry_after.isoformat().replace("+00:00", "Z") if retry_after else None
        update_download_list(
            output_dir, bvid, "paused", error="circuit open",
            error_code=FailureKind.RATE_LIMITED, retry_after=retry_after,
        )
        print("[pause] RATE_LIMITED; 请等待 retry_after 后重试")
        return 10
    RateLimiter().wait()
    audio_path = None
    try:
        print(f"[1/4] 下载音频: {bvid}")
        audio_path, meta = asyncio.run(fetch_audio(bvid, audio_dir, args.browser))
        print(f"  → {audio_path} ({audio_path.stat().st_size / 1024 / 1024:.1f} MB)")

        print(f"[2/4] ASR 转录（模型: {args.model}，language=auto）")
        srt_text = transcribe(audio_path, model_size=args.model, language=None)

        print("[3/4] 写入字幕文件")
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"{bvid}.md"
        output_path.write_text(srt_text, encoding="utf-8")

        from prepend_meta import prepend_meta
        print("[4/4] 拼 YAML frontmatter")
        if prepend_meta(meta, output_path):
            print(f"  → {output_path} (含 frontmatter)")
        else:
            print(f"  → {output_path} (frontmatter 失败，仅 SRT)")
        breaker.reset()
        update_download_list(output_dir, bvid, "success", output_path)
        print(f"\n完成 [OK] 字幕行数: {len(srt_text.splitlines())}")
        return 0
    except Exception as exc:
        kind = classify_failure(error=exc)
        if FailureKind.LOGIN_REQUIRED in str(exc):
            kind = FailureKind.LOGIN_REQUIRED
        breaker.record(kind)
        retry_after = breaker.retry_after
        retry_after = retry_after.isoformat().replace("+00:00", "Z") if retry_after else None
        status = "paused" if kind in (
            FailureKind.LOGIN_REQUIRED,
            FailureKind.RATE_LIMITED,
            FailureKind.BROWSER_START_FAILED,
        ) else "failed"
        update_download_list(
            output_dir, bvid, status, error=str(exc), error_code=kind,
            retry_after=retry_after,
        )
        if kind == FailureKind.LOGIN_REQUIRED:
            print(f"[pause] LOGIN_REQUIRED; 请先在 {args.browser.title()} 登录 B 站后重试")
            return 11
        if kind == FailureKind.RATE_LIMITED:
            print("[pause] RATE_LIMITED; 请等待 retry_after 后重试")
            return 10
        if kind == FailureKind.BROWSER_START_FAILED:
            print("[pause] BROWSER_START_FAILED")
            return 12
        raise
    finally:
        if audio_path and not args.keep_audio and audio_path.exists():
            audio_path.unlink()
            print(f"[清理] 删除临时音频: {audio_path}")


if __name__ == "__main__":
    main()
