"""Xiaohongshu note discovery and media download."""

import asyncio
import json
import os
import re
import shutil
import subprocess
import urllib.request
from pathlib import Path
from urllib.parse import urlparse


USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/138.0.0.0 Safari/537.36"


class XiaohongshuError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def parse_note_id(url: str) -> str:
    parts = [part for part in urlparse(url).path.split("/") if part]
    for marker in ("explore", "board"):
        if marker in parts:
            index = parts.index(marker)
            if index + 1 < len(parts) and parts[index + 1]:
                return parts[index + 1]
    raise XiaohongshuError("NOTE_NOT_FOUND", "无法从小红书 URL 解析笔记 ID")


def _first(state: dict, *keys):
    for key in keys:
        value = state.get(key)
        if value not in (None, ""):
            return value
    return None


def normalize_metadata(state: dict, source_url: str) -> dict:
    note_id = parse_note_id(source_url)
    state = state if isinstance(state, dict) else {}
    result = {
        "platform": "xiaohongshu",
        "note_id": note_id,
        "url": f"https://www.xiaohongshu.com/explore/{note_id}",
    }
    mappings = {
        "title": ("title",),
        "uploader": ("uploader", "author", "nickname"),
        "description": ("description", "desc"),
        "published_label": ("published_label", "published_at", "time"),
        "note_type": ("note_type",),
        "asset_count": ("asset_count",),
    }
    for output_key, keys in mappings.items():
        value = _first(state, *keys)
        if value not in (None, ""):
            result[output_key] = value
    return result


def classify_assets(video_urls: list[str], image_urls: list[str]) -> tuple[str, list[str]]:
    videos = list(dict.fromkeys(url for url in video_urls if url))
    if videos:
        return "video", [videos[0]]
    images = list(dict.fromkeys(url for url in image_urls if url))
    if images:
        return "image", images
    raise XiaohongshuError("ASSET_NOT_FOUND", "页面未发现小红书媒体资源")


def select_note_images(images: list[dict]) -> list[str]:
    selected = []
    seen = set()
    for image in images:
        url = image.get("url")
        if not url or url in seen:
            continue
        if int(image.get("width") or 0) < 500 or int(image.get("height") or 0) < 500:
            continue
        if "sns-webpic" not in url:
            continue
        seen.add(url)
        selected.append(url)
    return selected


def scaled_dimensions(width: int, height: int) -> tuple[int, int]:
    return max(1, int(width * 0.5)), max(1, int(height * 0.5))


def _page_state_script():
    return """() => {
        const title = document.querySelector('#detail-title')?.innerText?.trim() || '';
        const content = document.querySelector('.detail-desc, .note-content')?.innerText?.trim() || '';
        const description = content.startsWith(title) ? content.slice(title.length).trim() : content;
        const note = document.querySelector('#noteContainer, .note-container') || document;
        const author = Array.from(document.querySelectorAll('a[href*="/user/profile/"]'))
          .map(a => (a.innerText || '').trim())
          .find(text => text && text !== '我' && text.length < 80) || '';
        const published = (content.match(/(?:编辑于|发布于)?\\d{1,2}-\\d{1,2}[^\\n]*/) || [''])[0].trim();
        const videos = Array.from(note.querySelectorAll('video'))
          .map(video => video.currentSrc || video.src || '')
          .filter(Boolean);
        const images = Array.from(note.querySelectorAll('.swiper img, img')).map(image => ({
          url: image.currentSrc || image.src || '',
          width: image.naturalWidth || image.width || 0,
          height: image.naturalHeight || image.height || 0,
        }));
        return {title, description, author, published, videos, images};
    }"""


async def _discover_page(source_url: str, profile_dir: Path | None = None):
    from playwright.async_api import async_playwright

    async with async_playwright() as playwright:
        if profile_dir:
            context = await playwright.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir), headless=True, user_agent=USER_AGENT
            )
            close_context = True
        else:
            browser = await playwright.chromium.launch(headless=True)
            context = await browser.new_context(user_agent=USER_AGENT)
            close_context = False
        page = context.pages[0] if context.pages else await context.new_page()
        video_responses = []

        async def on_response(response):
            content_type = response.headers.get("content-type", "")
            if content_type.startswith("video/"):
                video_responses.append(response.url)

        page.on("response", on_response)
        try:
            await page.goto(source_url, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(2500)
            if urlparse(page.url).path.rstrip("/") == "/login":
                return {"login_required": True}
            state = await page.evaluate(_page_state_script())
            state["videos"] = list(dict.fromkeys(state.get("videos", []) + video_responses))
            return state
        finally:
            await context.close()


def discover_note(url: str, profile_dir: Path | None = None) -> tuple[dict, str | None, list[str]]:
    parse_note_id(url)
    env_profile = os.environ.get("XHS_SFETCH_CHROME_PROFILE")
    profile = profile_dir or (Path(env_profile).expanduser() if env_profile else None)
    login_required = False
    try:
        state = asyncio.run(_discover_page(url))
        login_required = bool(state.get("login_required"))
    except Exception:
        state = {}
    if not state.get("title") and profile:
        try:
            state = asyncio.run(_discover_page(url, profile))
            login_required = login_required or bool(state.get("login_required"))
        except Exception:
            state = {}
    if login_required and not state.get("title"):
        raise XiaohongshuError("LOGIN_REQUIRED", "小红书页面跳转到登录页，请配置已登录 profile")
    image_urls = select_note_images(state.get("images", []))
    note_type, assets = classify_assets(state.get("videos", []), image_urls)
    metadata = normalize_metadata(
        {
            "title": state.get("title"),
            "description": state.get("description"),
            "uploader": state.get("author"),
            "published_label": state.get("published"),
            "note_type": note_type,
            "asset_count": len(assets),
        },
        url,
    )
    return metadata, assets[0] if note_type == "video" else None, [] if note_type == "video" else assets


def _download_one(url: str, path: Path, referer: str) -> None:
    headers = {"User-Agent": USER_AGENT, "Referer": referer}
    probe = urllib.request.Request(url, headers={**headers, "Range": "bytes=0-1023"})
    try:
        with urllib.request.urlopen(probe, timeout=30) as response:
            if response.status not in (200, 206):
                raise XiaohongshuError("ASSET_DOWNLOAD_FAILED", "媒体 Range 探测失败")
            response.read(1024)
        request = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(request, timeout=120) as response, path.open("wb") as target:
            shutil.copyfileobj(response, target, 1024 * 1024)
    except XiaohongshuError:
        raise
    except Exception as exc:
        path.unlink(missing_ok=True)
        raise XiaohongshuError("ASSET_DOWNLOAD_FAILED", f"媒体下载失败: {exc}") from exc


def _resize_image(path: Path) -> None:
    scaled_path = path.with_name(path.name + ".scaled.webp")
    result = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(path),
            "-vf",
            "scale=trunc(iw*0.5):trunc(ih*0.5):flags=lanczos",
            "-frames:v",
            "1",
            str(scaled_path),
        ],
        capture_output=True,
    )
    if result.returncode:
        scaled_path.unlink(missing_ok=True)
        detail = result.stderr.decode(errors="ignore")[-500:]
        raise XiaohongshuError("IMAGE_RESIZE_FAILED", detail or "图片缩放失败")
    scaled_path.replace(path)


def download_assets(note: dict, video_url: str | None, image_urls: list[str], output_dir: Path) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        if video_url:
            path = output_dir / "video.mp4.tmp"
            _download_one(video_url, path, note["url"])
            path.replace(output_dir / "video.mp4")
            return {"asset_count": 1, "asset_files": ["video.mp4"]}
        if not image_urls:
            raise XiaohongshuError("ASSET_NOT_FOUND", "没有可下载的图文资源")
        image_dir = output_dir / "images"
        image_dir.mkdir(exist_ok=True)
        files = []
        for index, url in enumerate(image_urls, 1):
            path = image_dir / f"{index:02d}.webp.tmp"
            _download_one(url, path, note["url"])
            _resize_image(path)
            final_path = image_dir / f"{index:02d}.webp"
            path.replace(final_path)
            files.append(str(final_path.relative_to(output_dir)).replace("\\", "/"))
        return {"asset_count": len(files), "asset_files": files}
    except Exception:
        shutil.rmtree(output_dir, ignore_errors=True)
        raise
