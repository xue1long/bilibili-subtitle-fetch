from .models import SubtitleTask, TaskStatus


SUCCESS_STATUSES = {"success", "native_success", "asr_success"}
PAUSED_STATUSES = {"paused"}


def plan(videos, records=None):
    """Deduplicate discovered videos and create resumable subtitle tasks."""
    records = records or {}
    result = []
    seen = set()
    for video in videos:
        if not video.bvid or video.bvid in seen:
            continue
        seen.add(video.bvid)
        record = records.get(video.bvid, {})
        status = record.get("status")
        if status in SUCCESS_STATUSES:
            continue
        if status in PAUSED_STATUSES:
            result.append(SubtitleTask(video, TaskStatus.PAUSED, record.get("error")))
            continue
        if status == "failed":
            result.append(SubtitleTask(video, TaskStatus.FAILED, record.get("error")))
            continue
        if status == "no_subtitle":
            result.append(SubtitleTask(video, TaskStatus.NO_SUBTITLE, record.get("error")))
            continue
        result.append(SubtitleTask(video, TaskStatus.QUEUED, record.get("error")))
    return result
