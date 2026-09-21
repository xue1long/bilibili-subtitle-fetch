import asyncio
import os
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .models import Video, normalize_bvid


async def _discover(url: str):
    from playwright.async_api import async_playwright
    from runtime_paths import chrome_profile_dir
    profile = os.environ.get("BILIBILI_SFETCH_CHROME_PROFILE") or str(chrome_profile_dir())
    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir=profile, executable_path=p.chromium.executable_path,
            headless=True, args=["--disable-blink-features=AutomationControlled"],
        )
        page = await context.new_page()
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(1500)
        links = []
        seen = set()
        query = dict(parse_qsl(urlsplit(url).query, keep_blank_values=True))
        media_id = query.get("fid")
        if not media_id:
            raise RuntimeError("收藏夹 URL 缺少 fid")
        for page_number in range(1, 101):
            api_url = f"https://api.bilibili.com/x/v3/fav/resource/list?media_id={media_id}&pn={page_number}&ps=20&platform=web"
            data = await page.evaluate("""async (url) => {
                const r = await fetch(url, {credentials: 'include'});
                return await r.json();
            }""", api_url)
            medias = ((data or {}).get("data") or {}).get("medias") or []
            if not medias:
                break
            for item in medias:
                bvid = normalize_bvid(item.get("bvid"))
                if bvid and bvid not in seen:
                    seen.add(bvid)
                    links.append({"href": f"https://www.bilibili.com/video/{bvid}", "title": item.get("title", "")})
            if not ((data or {}).get("data") or {}).get("has_more"):
                break
        await context.close()
    seen = set()
    result = []
    for item in links:
        bvid = normalize_bvid(item.get("href"))
        if bvid and bvid not in seen:
            seen.add(bvid)
            result.append(Video(bvid=bvid, title=item.get("title", "").strip(), source_type="favorite", source_url=url))
    return result


def _page_url(url: str, page_number: int) -> str:
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["pn"] = str(page_number)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def discover(url: str):
    return asyncio.run(_discover(url))
