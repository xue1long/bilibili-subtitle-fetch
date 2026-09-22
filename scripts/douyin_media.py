"""Anonymous-first Douyin page resolution and video download."""

import asyncio
import json
import re
import shutil
import subprocess
import sys
import urllib.request
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse


USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/138.0.0.0 Safari/537.36"
HEADERS = {"User-Agent": USER_AGENT, "Referer": "https://www.douyin.com/"}
MEDIA_RE = re.compile(r'"playAddr"\s*:\s*"([^"\\]*(?:\\.[^"\\]*)*)"')
ID_RE = re.compile(r'"(?:aweme_id|awemeId|video_id)"\s*:\s*"?([0-9]+)')


class DouyinMediaError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def parse_video_id(value: str) -> str:
    parsed = urlparse(value)
    modal_id = parse_qs(parsed.query).get("modal_id", [None])[0]
    if modal_id and modal_id.isdigit():
        return modal_id
    match = re.search(r"/(?:video|note)/(\d+)(?:/|$)", parsed.path)
    if match:
        return match.group(1)
    if value.isdigit():
        return value
    raise DouyinMediaError("VIDEO_NOT_FOUND", "无法从 URL 解析抖音视频 ID")


def _first(data, *keys):
    if not isinstance(data, dict):
        return None
    for key in keys:
        value = data.get(key)
        if value not in (None, ""):
            return value
    return None


def extract_page_metadata(state: dict, source_url: str) -> dict:
    video_id = parse_video_id(source_url)
    detail = state.get("aweme_detail", state) if isinstance(state, dict) else {}
    author = detail.get("author", {}) if isinstance(detail, dict) else {}
    title = _first(detail, "title", "desc")
    result = {
        "platform": "douyin",
        "video_id": str(_first(detail, "aweme_id", "awemeId", "video_id") or video_id),
        "url": f"https://www.douyin.com/video/{video_id}",
    }
    if title:
        result["title"] = title
        result["description"] = _first(detail, "desc") or title
    uploader = _first(author, "nickname", "unique_id") or _first(detail, "nickname")
    if uploader:
        result["uploader"] = uploader
    create_time = _first(detail, "create_time", "createTime", "publish_time")
    if create_time:
        try:
            result["video_published_at"] = datetime.fromtimestamp(int(create_time)).strftime("%Y-%m-%d")
        except (TypeError, ValueError, OSError):
            pass
    return result


def _decode_json_string(value: str) -> str:
    try:
        return json.loads(f'"{value}"')
    except (json.JSONDecodeError, UnicodeDecodeError):
        return value.replace("\\/", "/")


def extract_media_url_from_html(html: str, video_id: str) -> str | None:
    """Return only a media candidate preceded by the requested aweme ID."""
    for match in MEDIA_RE.finditer(html):
        before = html[max(0, match.start() - 4096):match.start()]
        if re.search(rf'"(?:aweme_id|awemeId|video_id|modal_id)"\s*:\s*"?{re.escape(video_id)}"?', before):
            return _decode_json_string(match.group(1))
    return None


def _select_network_media_url(urls: list[str], html: str, video_id: str) -> str | None:
    """Use a sole observed video response only when the page identifies the target."""
    if len(urls) == 1 and video_id in html:
        return urls[0]
    return None


def _metadata_from_html(html: str, source_url: str, video_id: str) -> dict:
    result = extract_page_metadata({}, source_url)
    target = re.search(
        rf'"(?:aweme_id|awemeId|video_id)"\s*:\s*"?{re.escape(video_id)}"?.{{0,4096}}',
        html,
    )
    if not target:
        return result
    snippet = target.group(0)
    for key, output_key in (("desc", "description"), ("nickname", "uploader")):
        match = re.search(rf'"{key}"\s*:\s*"((?:\\.|[^"\\])*)"', snippet)
        if match:
            value = _decode_json_string(match.group(1))
            result[output_key] = value
            if output_key == "description" and "title" not in result:
                result["title"] = value
    return result


def _download_url(url: str, output_path: Path) -> None:
    probe = urllib.request.Request(url, headers={**HEADERS, "Range": "bytes=0-1023"})
    try:
        with urllib.request.urlopen(probe, timeout=30) as response:
            content_type = response.headers.get("Content-Type", "")
            if response.status not in (200, 206) or ("video" not in content_type and "octet-stream" not in content_type):
                raise DouyinMediaError("VIDEO_DOWNLOAD_FAILED", "媒体响应不是可下载视频")
            response.read(1024)
    except DouyinMediaError:
        raise
    except Exception as exc:
        raise DouyinMediaError("VIDEO_DOWNLOAD_FAILED", f"媒体 Range 探测失败: {exc}") from exc

    request = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(request, timeout=120) as response, output_path.open("wb") as target:
            shutil.copyfileobj(response, target, length=1024 * 1024)
    except Exception as exc:
        output_path.unlink(missing_ok=True)
        raise DouyinMediaError("VIDEO_DOWNLOAD_FAILED", f"视频流下载失败: {exc}") from exc


async def _anonymous_page(source_url: str, video_id: str):
    from playwright.async_api import async_playwright

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page(user_agent=USER_AGENT)
        media_responses = []

        async def on_response(response):
            content_type = response.headers.get("content-type", "")
            if content_type.startswith("video/"):
                media_responses.append(response.url)

        page.on("response", on_response)
        try:
            await page.goto(source_url, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(3500)
            html = await page.content()
            state = await page.evaluate(
                """() => {
                    for (const key of ['__INITIAL_STATE__', '__NEXT_DATA__', '_ROUTER_DATA__']) {
                        const value = window[key];
                        if (value && typeof value === 'object') return value;
                    }
                    return {};
                }"""
            )
            media_url = extract_media_url_from_html(html, video_id)
            if not media_url:
                media_url = _select_network_media_url(media_responses, html, video_id)
            metadata = extract_page_metadata(state if isinstance(state, dict) else {}, source_url)
            metadata.update({k: v for k, v in _metadata_from_html(html, source_url, video_id).items() if k not in metadata})
            return media_url, metadata
        finally:
            await browser.close()


def _try_ytdlp(source_url: str, output_path: Path) -> bool:
    command = [sys.executable, "-m", "yt_dlp", "--no-playlist", "--no-warnings", "-o", str(output_path), source_url]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=180)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0 and output_path.exists() and output_path.stat().st_size > 0


def download_video(url: str, output_path: Path, profile_dir: Path) -> dict:
    video_id = parse_video_id(url)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        media_url, metadata = asyncio.run(_anonymous_page(url, video_id))
        if media_url:
            _download_url(media_url, output_path)
            return metadata
    except Exception:
        pass

    if _try_ytdlp(url, output_path):
        return extract_page_metadata({}, url)

    # ponytail: one authenticated fallback, no cookie persistence beyond the user-selected browser profile.
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir), headless=True, user_agent=USER_AGENT
            )
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(3500)
            html = page.content()
            media_url = extract_media_url_from_html(html, video_id)
            metadata = _metadata_from_html(html, url, video_id)
            context.close()
        if media_url:
            _download_url(media_url, output_path)
            return metadata
    except Exception:
        pass
    raise DouyinMediaError("LOGIN_REQUIRED", "匿名解析和登录态 fallback 都未找到目标视频")
