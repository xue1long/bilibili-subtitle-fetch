from .models import SubtitleTask, TaskStatus


SUCCESS_STATUSES = {"success", "native_success", "asr_success"}
PAUSED_STATUSES = {"paused"}


def plan(videos, records=None, is_done=None):
    """Deduplicate discovered videos and create resumable subtitle tasks.

    `is_done(bvid)` 判定产物是否已经存在。跨机共享产物根时，产物文件本身就是
    "已产出"的事实依据（见 docs/adr/0002），用它兜住各机私有的任务库。
    """
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
        if is_done and is_done(video.bvid):
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
