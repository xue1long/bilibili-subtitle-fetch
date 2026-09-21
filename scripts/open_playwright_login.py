import asyncio
from pathlib import Path
from playwright.async_api import async_playwright


async def main():
    profile = Path(__file__).resolve().parents[1] / ".chrome-bilibili"
    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir=str(profile),
            executable_path=p.chromium.executable_path,
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto("https://www.bilibili.com/video/BV1vqeF62EQS", wait_until="domcontentloaded")
        print("浏览器已打开，项目配置目录:", profile, flush=True)
        await asyncio.sleep(600)
        await context.close()


asyncio.run(main())
