from dataclasses import dataclass


@dataclass(frozen=True)
class Video:
    bvid: str
    title: str = ""
    uploader: str = ""
    source_type: str = ""
    source_id: str = ""
    source_url: str = ""


def normalize_bvid(value: str) -> str:
    import re
    match = re.search(r"(BV[0-9A-Za-z]{10})", str(value or ""))
    return match.group(1) if match else ""
