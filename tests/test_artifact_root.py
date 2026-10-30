import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

from pipeline.planner import plan
from runtime_paths import default_output_dir, project_root, raw_dir


class ArtifactRootTest(unittest.TestCase):
    def test_default_raw_dir_is_inside_repo(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("BILIBILI_SFETCH_RAW_DIR", None)
            self.assertEqual(raw_dir(), project_root() / "10_raw")

    def test_raw_dir_follows_env_override(self):
        with patch.dict(os.environ, {"BILIBILI_SFETCH_RAW_DIR": "D:/Somewhere/产物根"}):
            self.assertEqual(raw_dir(), Path("D:/Somewhere/产物根"))

    def test_raw_dir_expands_env_variables(self):
        with patch.dict(os.environ, {"BILIBILI_SFETCH_RAW_DIR": "%OneDriveConsumer%/009-视频笔记",
                                    "OneDriveConsumer": "D:/OneDrive"}):
            self.assertEqual(raw_dir(), Path("D:/OneDrive/009-视频笔记"))

    def test_unexpanded_env_var_is_rejected(self):
        with patch.dict(os.environ, {"BILIBILI_SFETCH_RAW_DIR": "%NotDefinedAnywhere%/x"}):
            with self.assertRaises(RuntimeError):
                raw_dir()

    def test_default_output_dir_follows_raw_dir(self):
        with patch.dict(os.environ, {"BILIBILI_SFETCH_RAW_DIR": "D:/Somewhere/产物根"}):
            self.assertEqual(default_output_dir(), Path("D:/Somewhere/产物根/01_B站视频转录"))


class PlannerArtifactTest(unittest.TestCase):
    def _video(self, bvid):
        class V:
            pass

        v = V()
        v.bvid = bvid
        return v

    def test_existing_artifact_is_skipped(self):
        tasks = plan([self._video("BV1")], {}, is_done=lambda bvid: bvid == "BV1")
        self.assertEqual(tasks, [])

    def test_missing_artifact_still_planned(self):
        tasks = plan([self._video("BV1")], {}, is_done=lambda bvid: False)
        self.assertEqual(len(tasks), 1)

    def test_without_is_done_behaviour_unchanged(self):
        tasks = plan([self._video("BV1")], {})
        self.assertEqual(len(tasks), 1)


if __name__ == "__main__":
    unittest.main()
