from dataclasses import dataclass
from pathlib import Path
import os

from bili_guard import FailureKind


@dataclass
class BackendResult:
    status: str
    body: list | None = None
    error: str = ""
    # 浏览器运行时元数据（window.__INITIAL_STATE__.videoData），由 Playwright
    # 在 page 关闭前同步取出，供调用方做元数据 enrichment（带登录态、零额外网络请求）。
    meta_state: dict | None = None


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
            await page.wait_for_timeout(1500)
            # 字幕语言项位于折叠弹层内，Playwright 的可见性点击点不到；
            # 改用 JS 派发 click（与 selenium 后端一致），绕过可见性限制。
            clicked = await page.evaluate(
                """
                () => {
                    const fire = (el) => el && el.dispatchEvent(
                        new MouseEvent('click', {bubbles: true, cancelable: true}));
                    const btn = document.querySelector('.bpx-player-ctrl-btn.bpx-player-ctrl-subtitle')
                             || document.querySelector('.bpx-player-ctrl-subtitle');
                    fire(btn);
                    // 弹层展开后才可点击语言项：优先点容器，退回点文本节点
                    let items = Array.from(document.querySelectorAll('.bpx-player-ctrl-subtitle-language-item'));
                    if (!items.length) items = Array.from(document.querySelectorAll('.bpx-player-ctrl-subtitle-language-item-text'));
                    if (!items.length) return 0;
                    fire(items[0]);
                    return items.length;
                }
                """
            )
            # 轮询等待字幕响应被网络监听捕获（最多 ~14s）
            for _ in range(28):
                if captured:
                    break
                await page.wait_for_timeout(500)
            await page.wait_for_timeout(2000)
            # 在 page 关闭前同步取出浏览器运行时元数据，供调用方做 enrichment。
            # 仅取 videoData 子块，规避整棵 __INITIAL_STATE__ 的序列化/体积问题。
            meta_state = None
            try:
                state = await page.evaluate(
                    "() => { const s = window.__INITIAL_STATE__; return s ? { videoData: s.videoData || null } : null; }"
                )
                if isinstance(state, dict) and state.get("videoData"):
                    meta_state = state
            except Exception:
                meta_state = None
        finally:
            await context.close()
    if not captured:
        return BackendResult(FailureKind.NO_SUBTITLE, error="未捕获到 AI 字幕响应")
    return BackendResult("success", body=captured[-1], meta_state=meta_state)
