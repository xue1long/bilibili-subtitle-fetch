from dataclasses import dataclass
from pathlib import Path
import os

from bili_guard import FailureKind


@dataclass
class BackendResult:
    status: str
    body: list | None = None
    error: str = ""


async def extract(page_url: str, profile_dir: Path, executable_path: str, timeout_seconds: int = 30) -> BackendResult:
    """Open a video page and return a normalized native subtitle result."""
    from playwright.async_api import async_playwright
    from subtitle_extractor import _subtitle_body_from_payload

    captured = []
    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir), executable_path=executable_path,
            headless=True, args=["--disable-blink-features=AutomationControlled"],
        )
        page = context.pages[0] if context.pages else await context.new_page()

        async def on_response(response):
            if "aisubtitle.hdslb.com" not in response.url and "ai_subtitle" not in response.url:
                return
            try:
                body = _subtitle_body_from_payload(await response.json())
                if body:
                    captured.append(body)
            except Exception:
                pass

        page.on("response", on_response)
        try:
            await page.goto(page_url, wait_until="domcontentloaded", timeout=timeout_seconds * 1000)
            await page.wait_for_timeout(8000)
            button = page.locator(".bpx-player-ctrl-subtitle")
            if await button.count() == 0:
                return BackendResult(FailureKind.NO_SUBTITLE, error="未找到字幕按钮或视频没有可用字幕")
            await button.click()
            await page.wait_for_timeout(1000)
            language = page.locator(".bpx-player-ctrl-subtitle-language-item-text").first
            if await language.count():
                await language.click()
            await page.wait_for_timeout(6000)
        finally:
            await context.close()
    if not captured:
        return BackendResult(FailureKind.NO_SUBTITLE, error="未捕获到 AI 字幕响应")
    return BackendResult("success", body=captured[-1])
