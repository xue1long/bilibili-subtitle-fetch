from .models import Video, normalize_bvid


def discover(value: str):
    bvid = normalize_bvid(value)
    if not bvid:
        raise ValueError(f"无法识别 BV 号: {value}")
    return [Video(bvid=bvid, source_type="single", source_url=value)]
