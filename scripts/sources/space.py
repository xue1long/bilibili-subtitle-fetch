from .models import Video, normalize_bvid


def discover(url: str, cookies_path=None):
    import yt_dlp
    options = {"quiet": True, "skip_download": True, "flat_playlist": True}
    if cookies_path:
        options["cookiefile"] = cookies_path
    with yt_dlp.YoutubeDL(options) as ydl:
        result = ydl.extract_info(url, download=False)
    videos = []
    for entry in result.get("entries") or []:
        bvid = normalize_bvid(entry.get("id") or entry.get("url"))
        if bvid:
            videos.append(Video(bvid=bvid, title=entry.get("title", ""), source_type="space", source_url=url))
    return _unique(videos)


def _unique(videos):
    seen = set()
    result = []
    for video in videos:
        if video.bvid not in seen:
            seen.add(video.bvid)
            result.append(video)
    return result
