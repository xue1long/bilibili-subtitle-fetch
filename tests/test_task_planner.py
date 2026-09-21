import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

from pipeline.models import TaskStatus
from pipeline.planner import plan
from sources.models import Video


class TaskPlannerTest(unittest.TestCase):
    def test_deduplicates_by_bvid_and_skips_success(self):
        videos = [Video("BV1234567890"), Video("BV1234567890"), Video("BVABCDEFGHIJ")]
        tasks = plan(videos, {"BV1234567890": {"status": "success"}})
        self.assertEqual([task.video.bvid for task in tasks], ["BVABCDEFGHIJ"])

    def test_keeps_paused_task_visible(self):
        task = plan([Video("BV1234567890")], {"BV1234567890": {"status": "paused", "error": "login"}})[0]
        self.assertEqual(task.status, TaskStatus.PAUSED)
        self.assertEqual(task.reason, "login")


if __name__ == "__main__":
    unittest.main()
