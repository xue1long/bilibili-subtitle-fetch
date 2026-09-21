from dataclasses import dataclass
from enum import Enum
from typing import Optional

from sources.models import Video


class TaskStatus(str, Enum):
    DISCOVERED = "discovered"
    QUEUED = "queued"
    NATIVE_SUCCESS = "native_success"
    NO_SUBTITLE = "no_subtitle"
    ASR_QUEUED = "asr_queued"
    PAUSED = "paused"
    FAILED = "failed"


@dataclass(frozen=True)
class SubtitleTask:
    video: Video
    status: TaskStatus = TaskStatus.QUEUED
    reason: Optional[str] = None
