"""Reusable audio-ASR subtitle rescue helpers."""

import asyncio
import subprocess
import urllib.request
from pathlib import Path
from typing import Optional, Tuple

from runtime_paths import chrome_profile_dir, edge_profile_dir
from bili_guard import check_login_state

M4S_HEADERS = {
    "Referer": "https://www.bilibili.com",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/138.0.0.0 Safari/537.36",
}

FETCH_DASH_JS = """
async () => {
    const s = window.__INITIAL_STATE__;
    if (!s || !s.videoData) return {error: "no INITIAL_STATE or videoData"};
    const v = s.videoData;
    const meta = {bv: s.bvid, bvid: s.bvid, cid: s.cid, title: v.title,
        duration: v.duration, uploader: v.owner && v.owner.name,
        view_count: v.stat && v.stat.view, like_count: v.stat && v.stat.like,
        description: v.desc,
        video_published_at: v.pubdate ? new Date(v.pubdate * 1000).toISOString().slice(0,10) : null};
    const r = await fetch(`https://api.bilibili.com/x/player/playurl?bvid=${meta.bvid}&cid=${meta.cid}&fnval=16&fnver=0&fourk=1&qn=80`, {credentials: 'include'});
    const dash = await r.json();
    if (dash.code !== 0) return {...meta, error: `DASH API code=${dash.code}: ${dash.message}`};
    const audios = dash.data && dash.data.dash && dash.data.dash.audio;
    const videos = dash.data && dash.data.dash && dash.data.dash.video;
    if (audios && audios.length) return {...meta, audioUrl: audios[0].baseUrl || audios[0].base_url};
    if (videos && videos.length) return {...meta, audioUrl: videos[0].baseUrl || videos[0].base_url, fallback: "video"};
    if (dash.data && dash.data.durl && dash.data.durl.length) return {...meta, audioUrl: dash.data.durl[0].url, fallback: "flv"};
    return {...meta, error: "no audio/video URL in DASH"};
}
"""


def format_timestamp(seconds: float) -> str:
    total = int(round(seconds * 1000))
    hours, rem = divmod(total, 3600000)
    minutes, rem = divmod(rem, 60000)
    secs, millis = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def format_srt(segments) -> str:
    rows = []
    for index, segment in enumerate(segments, 1):
        rows.extend([str(index), f"{format_timestamp(segment.start)} --> {format_timestamp(segment.end)}", segment.text.strip(), ""])
    return "\n".join(rows)


def download_m4s(url: str, out_path: Path) -> None:
    request = urllib.request.Request(url, headers=M4S_HEADERS)
    with urllib.request.urlopen(request, timeout=60) as response:
        out_path.write_bytes(response.read())


async def fetch_audio(bvid: str, out_dir: Path, browser: str = "chrome") -> Tuple[Path, dict]:
    from playwright.async_api import async_playwright
    out_dir.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as playwright:
        profile = chrome_profile_dir() if browser == "chrome" else edge_profile_dir()
        context = await playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile), executable_path=playwright.chromium.executable_path,
            headless=True, args=["--no-sandbox"],
        )
        page = await context.new_page()
        await page.goto(f"https://www.bilibili.com/video/{bvid}", wait_until="networkidle", timeout=60000)
        session = check_login_state(page)
        if not session.ok:
            await context.close()
            raise RuntimeError(session.reason)
        result = await page.evaluate(FETCH_DASH_JS)
        await context.close()
    if result.get("error"):
        raise RuntimeError(f"DASH fetch failed: {result['error']}")
    audio_path = out_dir / f"{bvid}.mp3"
    if result.get("fallback") in ("video", "flv"):
        source = result["audioUrl"]
        proc = subprocess.run(["ffmpeg", "-y", "-user_agent", M4S_HEADERS["User-Agent"], "-headers", "Referer: https://www.bilibili.com\r\n", "-i", source, "-vn", "-acodec", "libmp3lame", "-ab", "64k", str(audio_path)], capture_output=True)
    else:
        m4s = out_dir / f"{bvid}.m4s"
        download_m4s(result["audioUrl"], m4s)
        proc = subprocess.run(["ffmpeg", "-y", "-i", str(m4s), "-vn", "-acodec", "libmp3lame", "-ab", "64k", str(audio_path)], capture_output=True)
        m4s.unlink(missing_ok=True)
    if proc.returncode:
        raise RuntimeError(f"ffmpeg failed: {proc.stderr.decode(errors='ignore')[-500:]}")
    return audio_path, {k: v for k, v in result.items() if k not in ("audioUrl", "fallback")}


def transcribe(audio_path: Path, model_size: str = "small", language: Optional[str] = None) -> str:
    from faster_whisper import WhisperModel
    model = WhisperModel(model_size, device="auto", compute_type="auto")
    segments, _ = model.transcribe(str(audio_path), language=language, beam_size=5)
    return format_srt(list(segments))


def rescue_subtitle(bvid: str, audio_dir: Path, output_path: Path, browser: str = "chrome", model_size: str = "small") -> dict:
    audio_path, meta = asyncio.run(fetch_audio(bvid, audio_dir, browser))
    try:
        srt_text = transcribe(audio_path, model_size=model_size)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(srt_text, encoding="utf-8")
        return {"path": output_path, "meta": meta, "model": model_size}
    finally:
        audio_path.unlink(missing_ok=True)
