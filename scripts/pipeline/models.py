from dataclasses import dataclass
from enum import Enum
from typing import Optional

from sources.models import Video


class TaskStatus(str, Enum):
    """任务状态。

    取值必须与实际写入任务库（`data/subtitle_tasks.db` 的 `jobs.status`）的
    字符串**完全一致** —— planner 与 build_download_viewer 都按字符串匹配，
    枚举缺一项就会漏判。

    实际写入点：
    - `cli.py:90` 写 `native_success` / `asr_success`
    - `cli.py:92` 写 `paused` / `failed`
    - `cli.py:85` 写 `native_running`
    - `subtitle_extractor.py` / `rescue_one_subtitle.py` 写 `success` / `paused`
    """

    DISCOVERED = "discovered"
    QUEUED = "queued"
    NATIVE_RUNNING = "native_running"
    NATIVE_SUCCESS = "native_success"
    ASR_QUEUED = "asr_queued"
    ASR_RUNNING = "asr_running"
    ASR_SUCCESS = "asr_success"
    SUCCESS = "success"
    NO_SUBTITLE = "no_subtitle"
    PAUSED = "paused"
    FAILED = "failed"


#: 视为「已产出、跳过」的状态集合。旧入口写 `success`，统一入口写
#: `native_success` / `asr_success`。
SUCCESS_STATUSES = {
    TaskStatus.SUCCESS.value,
    TaskStatus.NATIVE_SUCCESS.value,
    TaskStatus.ASR_SUCCESS.value,
}


@dataclass(frozen=True)
class SubtitleTask:
    video: Video
    status: TaskStatus = TaskStatus.QUEUED
    reason: Optional[str] = None
