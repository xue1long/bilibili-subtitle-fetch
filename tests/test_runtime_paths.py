import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

import runtime_paths


class RuntimePathsTest(unittest.TestCase):
    def test_root_is_this_repository(self):
        self.assertEqual(runtime_paths.project_root(), Path(__file__).parents[1])

    def test_env_overrides_profile(self):
        old = os.environ.get("BILIBILI_SFETCH_EDGE_PROFILE")
        try:
            os.environ["BILIBILI_SFETCH_EDGE_PROFILE"] = r"C:\profile"
            self.assertEqual(runtime_paths.edge_profile_dir(), Path(r"C:\profile"))
        finally:
            if old is None:
                os.environ.pop("BILIBILI_SFETCH_EDGE_PROFILE", None)
            else:
                os.environ["BILIBILI_SFETCH_EDGE_PROFILE"] = old

    def test_chrome_profile_override(self):
        old = os.environ.get("BILIBILI_SFETCH_CHROME_PROFILE")
        try:
            os.environ["BILIBILI_SFETCH_CHROME_PROFILE"] = r"C:\chrome-profile"
            self.assertEqual(runtime_paths.chrome_profile_dir(), Path(r"C:\chrome-profile"))
        finally:
            if old is None:
                os.environ.pop("BILIBILI_SFETCH_CHROME_PROFILE", None)
            else:
                os.environ["BILIBILI_SFETCH_CHROME_PROFILE"] = old


if __name__ == "__main__":
    unittest.main()
