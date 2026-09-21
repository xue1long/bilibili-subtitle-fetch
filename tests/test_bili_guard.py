import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

from bili_guard import (  # noqa: E402
    CircuitBreaker,
    FailureKind,
    RateLimiter,
    classify_failure,
    check_login_state,
    install_subtitle_probe,
    read_subtitle_probe,
)


class FakeDriver:
    def __init__(self, url="https://www.bilibili.com/video/BVTEST123", text="", state=True):
        self.current_url = url
        self.text = text
        self.state = state

    def execute_script(self, script):
        if "document.body.innerText" in script:
            return self.text
        if "__INITIAL_STATE__" in script:
            return self.state
        if "querySelector" in script:
            return False
        return None


class BiliGuardTest(unittest.TestCase):
    def test_logged_out_profile_is_rejected(self):
        status = check_login_state(FakeDriver(text="请先登录"))
        self.assertFalse(status.ok)
        self.assertEqual(status.reason, FailureKind.LOGIN_REQUIRED)

    def test_initial_state_alone_is_not_login_proof(self):
        status = check_login_state(FakeDriver(text="普通视频页面", state=True))
        self.assertFalse(status.ok)
        self.assertEqual(status.reason, FailureKind.LOGIN_REQUIRED)

    def test_positive_account_surface_passes_preflight(self):
        status = check_login_state(FakeDriver(text="用户头像 个人中心", state=True))
        self.assertTrue(status.ok)

    def test_playwright_page_adapter_is_supported(self):
        class FakePage:
            url = "https://www.bilibili.com/"

            def evaluate(self, expression):
                if "innerText" in expression:
                    return "用户头像 个人中心"
                return True

        self.assertTrue(check_login_state(FakePage()).ok)

    def test_page_access_failure_is_not_login_failure(self):
        class BrokenDriver:
            current_url = ""

            def execute_script(self, script):
                raise RuntimeError("page unavailable")

        status = check_login_state(BrokenDriver())
        self.assertFalse(status.ok)
        self.assertEqual(status.reason, FailureKind.PAGE_LOAD_FAILED)

    def test_http_412_is_rate_limited(self):
        self.assertEqual(
            classify_failure(response_status=412), FailureKind.RATE_LIMITED
        )

    def test_status_and_payload_evidence_are_distinct(self):
        self.assertEqual(
            classify_failure(
                response_status=200,
                subtitle_probe={"request_observed": True, "payload_received": False},
            ),
            FailureKind.NO_SUBTITLE,
        )
        self.assertEqual(
            classify_failure(
                subtitle_probe={"request_observed": True, "payload_received": False},
            ),
            FailureKind.SUBTITLE_API_FAILED,
        )

    def test_unobserved_subtitle_request_has_its_own_failure_kind(self):
        self.assertEqual(
            classify_failure(subtitle_probe={"request_observed": False}),
            FailureKind.SUBTITLE_NOT_OBSERVED,
        )

    def test_probe_exposes_stage_fields_and_request_count(self):
        class ProbeDriver:
            def __init__(self):
                self.script = ""

            def execute_script(self, script):
                self.script = script
                if script.startswith("return window.__subtitleProbe"):
                    return {"request_observed": True, "hook_installed": True,
                            "page_ready": True, "button_found": True,
                            "click_dispatched": True, "request_count": 2}

        driver = ProbeDriver()
        install_subtitle_probe(driver)
        probe = read_subtitle_probe(driver)
        self.assertEqual(probe["request_count"], 2)
        self.assertTrue(all(probe[name] for name in (
            "request_observed", "hook_installed", "page_ready",
            "button_found", "click_dispatched",
        )))
        self.assertEqual(probe["response_status"], None)
        self.assertEqual(probe["subtitle_count"], 0)

    def test_rate_limit_status_is_preserved_over_later_success(self):
        self.assertEqual(
            classify_failure(subtitle_probe={
                "request_observed": True,
                "response_status": 412,
                "payload_received": True,
            }),
            FailureKind.RATE_LIMITED,
        )

    def test_valid_subtitle_body_reaches_success_path(self):
        self.assertEqual(
            classify_failure(subtitle_probe={
                "request_observed": True,
                "response_status": 200,
                "payload_received": True,
            }),
            FailureKind.UNKNOWN,
        )

    def test_rate_limiter_waits_between_starts(self):
        sleeps = []
        now = [100.0]
        limiter = RateLimiter(
            min_delay=8,
            max_delay=8,
            clock=lambda: now[0],
            sleeper=sleeps.append,
        )
        limiter.wait()
        now[0] = 101.0
        limiter.wait()
        self.assertEqual(sleeps, [7.0])

    def test_only_rate_limits_open_the_circuit(self):
        with tempfile.TemporaryDirectory() as directory:
            breaker = CircuitBreaker(
                Path(directory) / "guard.json",
                threshold=2,
                cooldown_seconds=900,
                clock=lambda: 100.0,
                wall_clock=lambda: datetime.fromtimestamp(100, timezone.utc),
            )
            for kind in (FailureKind.SUBTITLE_NOT_OBSERVED,
                         FailureKind.NO_SUBTITLE,
                         FailureKind.SUBTITLE_API_FAILED):
                breaker.record(kind)
                self.assertEqual(breaker.state, "CLOSED")
            breaker.record(FailureKind.RATE_LIMITED)
            breaker.record(FailureKind.RATE_LIMITED)
            self.assertEqual(breaker.state, "OPEN")
            self.assertFalse(breaker.allow())

    def test_text_only_rate_limit_and_non_http_failures_do_not_open_circuit(self):
        self.assertEqual(
            classify_failure(error="rate limit exceeded"),
            FailureKind.SUBTITLE_NOT_OBSERVED,
        )
        self.assertEqual(
            classify_failure(error="HTTP 412 from script exception"),
            FailureKind.SUBTITLE_NOT_OBSERVED,
        )
        with tempfile.TemporaryDirectory() as directory:
            breaker = CircuitBreaker(Path(directory) / "guard.json", threshold=1)
            for kind in (FailureKind.LOGIN_REQUIRED, FailureKind.BROWSER_START_FAILED):
                breaker.record(kind)
                self.assertEqual(breaker.state, "CLOSED")

    def test_subtitle_click_stages_survive_language_selection(self):
        from subtitle_extractor import _click_subtitle_button, _select_subtitle_language

        class ClickDriver:
            def __init__(self):
                self.probe = {"button_found": False, "click_dispatched": False}

            def execute_script(self, script):
                if "const btn" in script:
                    self.probe["button_found"] = True
                    self.probe["click_dispatched"] = True
                elif "const items" in script:
                    pass

        driver = ClickDriver()
        _click_subtitle_button(driver)
        _select_subtitle_language(driver)
        self.assertTrue(driver.probe["button_found"])
        self.assertTrue(driver.probe["click_dispatched"])

    def test_persisted_breaker_enters_half_open_after_cooldown(self):
        now = [100.0]
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "guard.json"
            clock = lambda: now[0]
            wall_clock = lambda: datetime.fromtimestamp(now[0], timezone.utc)
            breaker = CircuitBreaker(
                state_path, threshold=1, cooldown_seconds=900,
                clock=clock, wall_clock=wall_clock,
            )
            breaker.record(FailureKind.RATE_LIMITED)
            restarted = CircuitBreaker(
                state_path, threshold=1, cooldown_seconds=900,
                clock=clock, wall_clock=wall_clock,
            )
            self.assertFalse(restarted.allow())
            now[0] = 1000.0
            self.assertTrue(restarted.allow())
            self.assertEqual(restarted.state, "HALF_OPEN")

    def test_browser_failure_retries_once_without_recursion(self):
        with tempfile.TemporaryDirectory() as directory:
            from subtitle_extractor import SubtitleExtractor

            extractor = SubtitleExtractor(
                output_dir=Path(directory),
                enrich_with_meta=False,
                min_delay=0,
                max_delay=0,
            )

            def fail_once_then_fail_again(bvid):
                extractor.last_failure_kind = FailureKind.BROWSER_START_FAILED
                return None

            with patch.object(extractor, "_extract_single_once", side_effect=fail_once_then_fail_again) as once, \
                    patch("subtitle_extractor.time.sleep"):
                self.assertIsNone(extractor.extract_single("BVTEST123"))
            self.assertEqual(once.call_count, 2)


if __name__ == "__main__":
    unittest.main()
