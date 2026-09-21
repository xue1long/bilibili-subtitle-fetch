import asyncio
import json
from pathlib import Path
from playwright.async_api import async_playwright


async def main():
    root = Path(__file__).resolve().parents[1]
    profile = root / ".chrome-bilibili"
    out = root / "10_raw" / "01_B站视频转录" / "BV1vqeF62EQS.md"
    captured = []

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir=str(profile),
            executable_path=p.chromium.executable_path,
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = context.pages[0] if context.pages else await context.new_page()

        async def on_response(response):
            url = response.url
            if "aisubtitle.hdslb.com" in url or "ai_subtitle" in url:
                try:
                    payload = await response.json()
                    if isinstance(payload, dict) and payload.get("body"):
                        captured.append(payload)
                except Exception:
                    pass

        page.on("response", on_response)
        await page.goto("https://www.bilibili.com/video/BV1vqeF62EQS", wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(8000)
        button = page.locator(".bpx-player-ctrl-subtitle")
        await button.click(timeout=15000)
        await page.wait_for_timeout(1000)
        lang = page.locator(".bpx-player-ctrl-subtitle-language-item-text").first
        if await lang.count():
            await lang.click()
        await page.wait_for_timeout(6000)
        await context.close()

    if not captured:
        raise SystemExit("未捕获字幕接口响应")
    body = captured[-1]["body"]
    lines = []
    for i, item in enumerate(body, 1):
        start = float(item.get("from", 0))
        end = float(item.get("to", start + 2))
        def ts(value):
            ms = round(value * 1000)
            h, ms = divmod(ms, 3600000)
            m, ms = divmod(ms, 60000)
            s, ms = divmod(ms, 1000)
            return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
        lines += [str(i), f"{ts(start)} --> {ts(end)}", item.get("content", "").strip(), ""]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"已保存 {len(body)} 条字幕: {out}")


asyncio.run(main())
