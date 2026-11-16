"""任务状态契约测试。

锁死两件事：
1. `TaskStatus` 枚举必须覆盖**实际写入任务库**的每一个状态字符串 —— 少一项，
   planner / 看板就会漏判，把已完成的视频重新排队。
2. `SUCCESS_STATUSES` 只有一个定义来源（`pipeline.models`），planner 必须复用它，
   不能自己抄一份 —— 抄的那份迟早和枚举脱节。
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

from pipeline import models, planner
from pipeline.models import SUCCESS_STATUSES, TaskStatus


class TaskStatusEnumTest(unittest.TestCase):
    def test_enum_covers_every_status_written_to_the_store(self):
        """枚举成员必须覆盖 cli.py / subtitle_extractor.py 实际写入的值。"""
        written = {
            "native_running",
            "native_success",
            "asr_success",
            "paused",
            "failed",
            "success",
        }
        declared = {status.value for status in TaskStatus}
        self.assertTrue(
            written <= declared,
            f"TaskStatus 缺少实际写入的状态: {sorted(written - declared)}",
        )

    def test_asr_success_is_declared(self):
        """cli.py:90 会写 'asr_success'，枚举缺它就会漏判成功态。"""
        self.assertEqual(TaskStatus.ASR_SUCCESS.value, "asr_success")
        self.assertIn(TaskStatus.ASR_SUCCESS.value, SUCCESS_STATUSES)

    def test_success_statuses_contains_all_success_values(self):
        self.assertEqual(
            SUCCESS_STATUSES,
            {"success", "native_success", "asr_success"},
        )

    def test_success_statuses_excludes_non_success(self):
        for value in ("failed", "paused", "no_subtitle", "queued", "running"):
            self.assertNotIn(value, SUCCESS_STATUSES)

    def test_enum_values_are_unique(self):
        values = [status.value for status in TaskStatus]
        self.assertEqual(len(values), len(set(values)))


class SuccessStatusSingleSourceTest(unittest.TestCase):
    def test_planner_reuses_models_success_statuses(self):
        """planner 不能自己定义一份 SUCCESS_STATUSES。"""
        self.assertIs(planner.SUCCESS_STATUSES, models.SUCCESS_STATUSES)

    def test_planner_has_no_local_literal_copy(self):
        """防止有人重新在 planner 里手写一份字面量集合。"""
        import inspect

        source = inspect.getsource(planner)
        self.assertNotIn('"asr_success"', source)
        self.assertNotIn("'asr_success'", source)

    def test_status_map_in_viewer_covers_every_enum_value(self):
        """看板的状态映射必须能吃下枚举里的每一个值，否则展示层会漏状态。"""
        sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
        import build_download_viewer

        for status in TaskStatus:
            self.assertIn(
                status.value,
                build_download_viewer.STATUS_MAP,
                f"看板 STATUS_MAP 缺少状态 {status.value}",
            )


class PlannerBehaviourTest(unittest.TestCase):
    def test_skips_each_success_status(self):
        from sources.models import Video

        for value in sorted(SUCCESS_STATUSES):
            tasks = planner.plan(
                [Video("BV1234567890")],
                {"BV1234567890": {"status": value}},
            )
            self.assertEqual(tasks, [], f"状态 {value} 应被跳过")

    def test_asr_success_task_is_skipped(self):
        """回归用例：枚举补 ASR_SUCCESS 后，planner 必须认得它。"""
        from sources.models import Video

        tasks = planner.plan(
            [Video("BV1234567890")],
            {"BV1234567890": {"status": "asr_success"}},
        )
        self.assertEqual(tasks, [])


if __name__ == "__main__":
    unittest.main()
