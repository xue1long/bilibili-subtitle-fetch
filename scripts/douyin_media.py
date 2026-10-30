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
# 抖音是客户端渲染，初始 HTML 里没有 playAddr（正则命中 0 次），媒体只能从
# aweme/detail 接口拿；下面的正则只用于旧页面扫描的兜底路径。
MEDIA_RE = re.compile(r'"playAddr"\s*:\s*"([^"\\]*(?:\\.[^"\\]*)*)"')
ID_RE = re.compile(r'"(?:aweme_id|awemeId|video_id)"\s*:\s*"?([0-9]+)')
AWEME_DETAIL_PATH = "/aweme/v1/web/aweme/detail"


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


def canonical_video_url(video_id: str) -> str:
    """详情页地址：/video/<id> 才会触发 aweme/detail 接口。"""
    return f"https://www.douyin.com/video/{video_id}"


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


def _aweme_media_kind(detail: dict) -> str:
    """图文帖返回 image，视频帖返回 video。"""
    if detail.get("images"):
        return "image"
    if detail.get("media_type") == 2 or detail.get("aweme_type") in (2, 68):
        return "image"
    return "video"


def _collect_image_urls(detail: dict) -> list[str]:
    """每张图取一个地址：优先 download_url_list（原图），回退 url_list。"""
    urls: list[str] = []
    seen: set[str] = set()
    for image in detail.get("images") or []:
        if not isinstance(image, dict):
            continue
        for url in list(image.get("download_url_list") or []) + list(image.get("url_list") or []):
            if url and url not in seen:
                seen.add(url)
                urls.append(url)
                break
    return urls


def _collect_video_urls(detail: dict) -> list[str]:
    """视频帖取码率列表里的播放地址，再回退 play_addr。"""
    video = detail.get("video") or {}
    urls: list[str] = []
    seen: set[str] = set()
    for entry in video.get("bit_rate") or []:
        play = entry.get("play_addr") or {}
        for url in play.get("url_list") or []:
            if url and url not in seen:
                seen.add(url)
                urls.append(url)
    for url in (video.get("play_addr") or {}).get("url_list") or []:
        if url and url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


def parse_aweme_detail(payload: dict, video_id: str) -> dict | None:
    """把 aweme/detail 响应归一化成目标帖记录；aweme_id 不匹配时返回 None。"""
    if not isinstance(payload, dict):
        return None
    detail = payload.get("aweme_detail")
    if not isinstance(detail, dict):
        return None
    if str(detail.get("aweme_id") or "") != str(video_id):
        return None
    kind = _aweme_media_kind(detail)
    author = detail.get("author") or {}
    video = detail.get("video") or {}
    desc = detail.get("desc") or ""
    return {
        "video_id": str(video_id),
        "media_kind": kind,
        "title": desc,
        "description": desc,
        "uploader": author.get("nickname") or author.get("unique_id") or "",
        "create_time": detail.get("create_time"),
        "duration": video.get("duration") or 0,
        # 图文帖的 play_addr 指向背景音乐，不是内容本身，这里不取。
        "image_urls": _collect_image_urls(detail) if kind == "image" else [],
        "video_urls": _collect_video_urls(detail) if kind == "video" else [],
        "payload": payload,
    }


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


def _download_image(url: str, path: Path, referer: str) -> str:
    """下载一张图，返回与响应类型匹配的扩展名。"""
    headers = {"User-Agent": USER_AGENT, "Referer": referer}
    try:
        request = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(request, timeout=120) as response:
            content_type = response.headers.get("Content-Type", "")
            with path.open("wb") as target:
                shutil.copyfileobj(response, target, length=1024 * 1024)
    except Exception as exc:
        path.unlink(missing_ok=True)
        raise DouyinMediaError("ASSET_DOWNLOAD_FAILED", f"图片下载失败: {exc}") from exc
    if "webp" in content_type:
        return "webp"
    if "png" in content_type:
        return "png"
    if "jpeg" in content_type or "jpg" in content_type:
        return "jpg"
    return "webp"


def download_images(urls: list[str], output_dir: Path, referer: str = "https://www.douyin.com/") -> list[str]:
    """下载图文帖的全部图片，返回相对文件名列表。"""
    if not urls:
        raise DouyinMediaError("ASSET_NOT_FOUND", "抖音图文没有可下载的图片")
    output_dir.mkdir(parents=True, exist_ok=True)
    files: list[str] = []
    try:
        for index, url in enumerate(urls, 1):
            temp_path = output_dir / f"{index:02d}.tmp"
            extension = _download_image(url, temp_path, referer)
            final_path = output_dir / f"{index:02d}.{extension}"
            temp_path.replace(final_path)
            files.append(final_path.name)
    except Exception:
        shutil.rmtree(output_dir, ignore_errors=True)
        raise
    return files


_PAGE_STATE_SCRIPT = """() => {
    for (const key of ['__INITIAL_STATE__', '__NEXT_DATA__', '_ROUTER_DATA__']) {
        const value = window[key];
        if (value && typeof value === 'object') return value;
    }
    return {};
}"""


async def _open_page(playwright, profile_dir: Path | None):
    if profile_dir:
        context = await playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir), headless=True, user_agent=USER_AGENT
        )
    else:
        browser = await playwright.chromium.launch(headless=True)
        context = await browser.new_context(user_agent=USER_AGENT)
    page = context.pages[0] if context.pages else await context.new_page()
    return context, page


async def _capture_aweme_detail(video_id: str, profile_dir: Path | None = None, timeout_ms: int = 20000) -> dict | None:
    """打开详情页，抓取 aweme/detail 接口的 JSON 响应。

    接口在页面加载后数秒才返回，所以等事件而不是固定睡眠。
    """
    from playwright.async_api import async_playwright

    async with async_playwright() as playwright:
        context, page = await _open_page(playwright, profile_dir)
        payloads = []
        captured = asyncio.Event()

        async def on_response(response):
            if AWEME_DETAIL_PATH not in response.url:
                return
            try:
                payloads.append(await response.json())
            except Exception:
                pass
            finally:
                captured.set()

        page.on("response", on_response)
        try:
            await page.goto(canonical_video_url(video_id), wait_until="domcontentloaded", timeout=60000)
            # 该接口在页面加载后约 8 秒才返回，等事件而不是固定 sleep
            try:
                await asyncio.wait_for(captured.wait(), timeout=timeout_ms / 1000)
            except asyncio.TimeoutError:
                pass
            await page.wait_for_timeout(500)
        finally:
            await context.close()
    for payload in payloads:
        if isinstance(payload, dict) and payload.get("aweme_detail"):
            return payload
    return None


def resolve_post(url: str, profile_dir: Path | None = None) -> dict | None:
    """解析目标抖音帖；匿名接口优先，其次已登录 profile。"""
    video_id = parse_video_id(url)
    for use_profile in (False, True):
        profile = profile_dir if use_profile else None
        try:
            payload = asyncio.run(_capture_aweme_detail(video_id, profile))
        except Exception:
            payload = None
        if not payload:
            continue
        record = parse_aweme_detail(payload, video_id)
        if record:
            return record
    return None


async def _legacy_page(source_url: str, video_id: str, profile_dir: Path | None = None):
    """旧路径：扫描页面 HTML 与网络响应里的播放地址。"""
    from playwright.async_api import async_playwright

    async with async_playwright() as playwright:
        context, page = await _open_page(playwright, profile_dir)
        media_responses = []

        async def on_response(response):
            content_type = response.headers.get("content-type", "")
            if content_type.startswith("video/"):
                media_responses.append(response.url)

        page.on("response", on_response)
        try:
            await page.goto(canonical_video_url(video_id), wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(3500)
            html = await page.content()
            state = await page.evaluate(_PAGE_STATE_SCRIPT)
            media_url = extract_media_url_from_html(html, video_id)
            if not media_url:
                media_url = _select_network_media_url(media_responses, html, video_id)
            metadata = extract_page_metadata(state if isinstance(state, dict) else {}, source_url)
            metadata.update({k: v for k, v in _metadata_from_html(html, source_url, video_id).items() if k not in metadata})
            return media_url, metadata
        finally:
            await context.close()


def _try_ytdlp(source_url: str, output_path: Path) -> bool:
    command = [sys.executable, "-m", "yt_dlp", "--no-playlist", "--no-warnings", "-o", str(output_path), source_url]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=180)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0 and output_path.exists() and output_path.stat().st_size > 0


def download_video(url: str, output_path: Path, profile_dir: Path, post: dict | None = None) -> dict:
    video_id = parse_video_id(url)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if post is None:
        post = resolve_post(url, profile_dir)
    if post and post.get("media_kind") == "video":
        metadata = extract_page_metadata(post.get("payload") or {}, url)
        for media_url in post.get("video_urls") or []:
            try:
                _download_url(media_url, output_path)
                return metadata
            except DouyinMediaError:
                continue

    try:
        media_url, metadata = asyncio.run(_legacy_page(url, video_id))
    except Exception:
        media_url, metadata = None, {}
    if media_url:
        try:
            _download_url(media_url, output_path)
            return metadata
        except DouyinMediaError:
            pass

    if _try_ytdlp(url, output_path):
        return extract_page_metadata({}, url)

    # ponytail: one authenticated fallback, no cookie persistence beyond the user-selected browser profile.
    try:
        media_url, metadata = asyncio.run(_legacy_page(url, video_id, profile_dir))
    except Exception:
        media_url, metadata = None, {}
    if media_url:
        try:
            _download_url(media_url, output_path)
            return metadata
        except DouyinMediaError:
            pass
    raise DouyinMediaError("LOGIN_REQUIRED", "匿名解析和登录态 fallback 都未找到目标视频")
