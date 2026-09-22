"""Open a persistent Xiaohongshu profile for one-time manual login."""

import asyncio
import os
from pathlib import Path

from playwright.async_api import async_playwright


async def main():
    profile = Path(os.environ.get("XHS_SFETCH_CHROME_PROFILE", Path(__file__).resolve().parents[1] / ".chrome-xiaohongshu"))
    async with async_playwright() as playwright:
        context = await playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile), headless=False
        )
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto("https://www.xiaohongshu.com/", wait_until="domcontentloaded")
        print("请在打开的窗口中完成小红书登录；profile:", profile, flush=True)
        await asyncio.sleep(600)
        await context.close()


asyncio.run(main())
