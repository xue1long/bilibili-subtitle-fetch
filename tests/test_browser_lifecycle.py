import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from selenium.common.exceptions import TimeoutException

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

from bili_guard import FailureKind, SessionStatus  # noqa: E402
import run_subtitle_batch  # noqa: E402
from subtitle_extractor import SubtitleExtractor  # noqa: E402


class FakeDriver:
    def __init__(self, *, fail_get=False, get_error=None):
        self.fail_get = fail_get
        self.get_error = get_error
        self.urls = []
        self.page_load_timeouts = []
        self.quit_count = 0

    def set_page_load_timeout(self, timeout):
        self.page_load_timeouts.append(timeout)

    def get(self, url):
        if self.get_error:
            raise self.get_error
        if self.fail_get:
            raise RuntimeError("page load failed")
        self.urls.append(url)

    def execute_script(self, script):
        if "__capturedSubtitleData" in script:
            return {"body": [{"content": "hello", "from": 0, "to": 1}]}
        return {}

    def quit(self):
        self.quit_count += 1


class BrowserLifecycleTest(unittest.TestCase):
    def make_extractor(self, directory, **kwargs):
        return SubtitleExtractor(
            output_dir=Path(directory), enrich_with_meta=False,
            min_delay=0, max_delay=0, **kwargs,
        )

    def test_reuse_mode_starts_one_driver_and_quits_once(self):
        with tempfile.TemporaryDirectory() as directory:
            driver = FakeDriver()
            extractor = self.make_extractor(directory, reuse_browser=True)
            with patch("selenium.webdriver.Chrome", return_value=driver), \
                    patch("subtitle_extractor.check_login_state", return_value=SessionStatus(True)), \
                    patch("subtitle_extractor.time.sleep"), \
                    patch.object(extractor, "_update_db_subtitle_path"):
                self.assertTrue(extractor.extract_single("BV1"))
                self.assertTrue(extractor.extract_single("BV2"))
            self.assertEqual(driver.urls, [
                "https://www.bilibili.com/video/BV1",
                "https://www.bilibili.com/video/BV2",
            ])
            extractor.close()
            self.assertEqual(driver.quit_count, 1)

    def test_page_load_uses_a_finite_timeout(self):
        with tempfile.TemporaryDirectory() as directory:
            driver = FakeDriver()
            extractor = self.make_extractor(directory)
            with patch("selenium.webdriver.Chrome", return_value=driver), \
                    patch("subtitle_extractor.check_login_state", return_value=SessionStatus(True)), \
                    patch("subtitle_extractor.time.sleep"), \
                    patch.object(extractor, "_update_db_subtitle_path"):
                self.assertTrue(extractor.extract_single("BV1"))
            self.assertEqual(driver.page_load_timeouts, [30])

    def test_renderer_timeout_keeps_processing_loaded_page(self):
        with tempfile.TemporaryDirectory() as directory:
            driver = FakeDriver(get_error=TimeoutException("renderer timeout"))
            extractor = self.make_extractor(directory)
            with patch("selenium.webdriver.Chrome", return_value=driver), \
                    patch("subtitle_extractor.check_login_state", return_value=SessionStatus(True)), \
                    patch("subtitle_extractor.time.sleep"), \
                    patch.object(extractor, "_update_db_subtitle_path"):
                self.assertTrue(extractor.extract_single("BV1"))

    def test_close_is_idempotent(self):
        extractor = object.__new__(SubtitleExtractor)
        driver = FakeDriver()
        extractor._driver = driver
        extractor.close()
        extractor.close()
        self.assertEqual(driver.quit_count, 1)
        self.assertIsNone(extractor._driver)

    def test_non_reuse_mode_preserves_per_video_ownership(self):
        with tempfile.TemporaryDirectory() as directory:
            drivers = [FakeDriver(), FakeDriver()]
            extractor = self.make_extractor(directory)
            with patch("selenium.webdriver.Chrome", side_effect=drivers), \
                    patch("subtitle_extractor.check_login_state", return_value=SessionStatus(True)), \
                    patch("subtitle_extractor.time.sleep"), \
                    patch.object(extractor, "_update_db_subtitle_path"):
                self.assertTrue(extractor.extract_single("BV1"))
                self.assertTrue(extractor.extract_single("BV2"))
            self.assertEqual([driver.quit_count for driver in drivers], [1, 1])

    def test_startup_and_page_load_failures_restart_at_most_once(self):
        with tempfile.TemporaryDirectory() as directory:
            extractor = self.make_extractor(directory, reuse_browser=True)
            with patch.object(extractor, "_start_driver", side_effect=[RuntimeError("webdriver start"), FakeDriver()]) as start:
                with patch("subtitle_extractor.time.sleep"):
                    extractor.extract_single("BV1")
            self.assertEqual(start.call_count, 2)
            self.assertEqual(extractor.last_failure_kind, FailureKind.PAGE_LOAD_FAILED)

    def test_login_and_rate_limit_failures_do_not_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            extractor = self.make_extractor(directory, reuse_browser=True)
            with patch.object(extractor, "_start_driver", return_value=FakeDriver()) as start, \
                    patch("subtitle_extractor.check_login_state", return_value=SessionStatus(False, FailureKind.LOGIN_REQUIRED)), \
                    patch("subtitle_extractor.time.sleep"):
                extractor.extract_single("BV1")
            self.assertEqual(start.call_count, 1)

    def test_probe_resets_and_outcomes_have_durations(self):
        with tempfile.TemporaryDirectory() as directory:
            extractor = self.make_extractor(directory, reuse_browser=True)
            extractor._download_list["records"]["BV1"] = {"status": "success"}
            extractor.last_probe["request_observed"] = True
            extractor.extract_single("BV1")
            self.assertFalse(extractor.last_probe["request_observed"])
            self.assertGreaterEqual(extractor.last_duration_sec, 0)

    def test_batch_closes_extractor_in_finally(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bvids = root / "bvids.json"
            bvids.write_text(json.dumps({"keyword": "test", "videos": []}), encoding="utf-8")
            fake_extractor = type("FakeExtractor", (), {"close": unittest.mock.Mock()})()
            fake_module = type("FakeModule", (), {"SubtitleExtractor": lambda **kwargs: fake_extractor})
            with patch.object(run_subtitle_batch, "patch_selenium_edge", return_value=False), \
                    patch.object(run_subtitle_batch, "load_skill_module", return_value=fake_module), \
                    patch.object(run_subtitle_batch, "run_one", side_effect=RuntimeError("boom")), \
                    patch.object(sys, "argv", ["run_subtitle_batch.py", str(bvids), str(root), "edge"]):
                run_subtitle_batch.main()
            fake_extractor.close.assert_called_once_with()

    def test_batch_closes_extractor_when_result_log_write_raises(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bvids = root / "bvids.json"
            bvids.write_text(
                json.dumps({"keyword": "test", "videos": [{"bvid": "BV1"}]}),
                encoding="utf-8",
            )
            fake_extractor = type(
                "FakeExtractor",
                (),
                {
                    "close": unittest.mock.Mock(),
                    "last_probe": {},
                    "last_duration_sec": 0.1,
                    "browser_start_count": 1,
                    "browser_restart_count": 0,
                    "breaker": type("Breaker", (), {"state": "CLOSED"})(),
                },
            )()
            fake_module = type(
                "FakeModule",
                (),
                {"SubtitleExtractor": lambda **kwargs: fake_extractor},
            )
            with patch.object(run_subtitle_batch, "patch_selenium_edge", return_value=False), \
                    patch.object(run_subtitle_batch, "load_skill_module", return_value=fake_module), \
                    patch.object(run_subtitle_batch, "run_one", return_value=("success", "ok", None)), \
                    patch.object(run_subtitle_batch.json, "dump", side_effect=RuntimeError("log write failed")), \
                    patch.object(sys, "argv", ["run_subtitle_batch.py", str(bvids), str(root), "edge"]):
                with self.assertRaisesRegex(RuntimeError, "log write failed"):
                    run_subtitle_batch.main()
            fake_extractor.close.assert_called_once_with()

    def test_page_load_preflight_closes_cached_driver_before_recording_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            driver = FakeDriver()
            extractor = self.make_extractor(directory, reuse_browser=True)
            extractor._started_at = 0
            observed = {}

            def record_failure(*args):
                observed["driver"] = extractor._driver
                observed["quit_count"] = driver.quit_count

            with patch.object(extractor, "_start_driver", return_value=driver), \
                    patch("subtitle_extractor.check_login_state", return_value=SessionStatus(False, FailureKind.PAGE_LOAD_FAILED)), \
                    patch.object(extractor, "_record_failure", side_effect=record_failure), \
                    patch("subtitle_extractor.time.sleep"):
                self.assertIsNone(extractor._extract_single_once("BV1"))
            self.assertIsNone(observed["driver"])
            self.assertEqual(observed["quit_count"], 1)

    def test_secondary_skipped_path_sets_duration(self):
        with tempfile.TemporaryDirectory() as directory:
            extractor = self.make_extractor(directory)
            extractor._download_list["records"]["BV1"] = {"status": "success"}
            extractor._started_at = 100
            with patch("subtitle_extractor.time.monotonic", return_value=101):
                self.assertEqual(extractor._extract_single_once("BV1"), "SKIPPED")
            self.assertEqual(extractor.last_duration_sec, 1)


if __name__ == "__main__":
    unittest.main()
