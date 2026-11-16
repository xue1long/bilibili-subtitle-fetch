"""Open a persistent Xiaohongshu profile for one-time manual login.

Xiaohongshu redirects every request to /login without a signed-in session, so
metadata collection needs this profile. After logging in once, the cookies are
reused by ``platform_meta_browser.py --platform xiaohongshu --profile ...``.

This machine has no Chrome, only Edge, so the browser binary is resolved the
same way the metadata collector does it.
"""

import asyncio
import os
import sys
from pathlib import Path

from playwright.async_api import async_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))

from platform_meta_browser import find_browser

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36")


async def main() -> int:
    profile = Path(os.environ.get(
        "XHS_SFETCH_CHROME_PROFILE",
        Path(__file__).resolve().parents[1] / ".chrome-xiaohongshu"))
    profile.mkdir(parents=True, exist_ok=True)
    executable = find_browser()
    async with async_playwright() as playwright:
        context = await playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile), headless=False,
            executable_path=executable, user_agent=USER_AGENT,
            locale="zh-CN", viewport={"width": 1280, "height": 900})
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto("https://www.xiaohongshu.com/",
                        wait_until="domcontentloaded")
        print("请在打开的窗口中完成小红书登录；profile:", profile, flush=True)
        print("登录完成后本脚本会自动检测并保存。", flush=True)
        # Poll until the page is signed in, then close so the profile is
        # flushed to disk. Killing the process instead would lose the
        # cookies.
        for _ in range(300):
            await asyncio.sleep(2)
            try:
                signed_in = await page.evaluate(
                    """() => {
                        const text = document.body ? document.body.innerText : '';
                        return !/^登录$/m.test(text.slice(0, 200))
                               && !!document.querySelector(
                                   '.user .link-wrapper, .user-info, [class*=user]');
                    }"""
                )
            except Exception:
                continue
            if signed_in:
                print("检测到登录成功，正在保存 profile...", flush=True)
                break
        else:
            print("未检测到登录，超时退出。", flush=True)
        await context.close()
    print("profile 已保存。", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))