import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

from bili_guard import (  # noqa: E402
    CircuitBreaker,
    CoverStatus,
    FailureKind,
    FetchPolicy,
    RateLimiter,
    classify_failure,
    classify_view_response,
    check_login_state,
    fetch_covers,
    install_subtitle_probe,
    normalize_cover_url,
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


class CoverFetchPolicyTest(unittest.TestCase):
    def test_protocol_relative_and_http_covers_become_https(self):
        self.assertEqual(normalize_cover_url("//i0.hdslb.com/a.jpg"),
                         "https://i0.hdslb.com/a.jpg")
        self.assertEqual(normalize_cover_url("http://i1.hdslb.com/b.jpg"),
                         "https://i1.hdslb.com/b.jpg")
        self.assertEqual(normalize_cover_url("https://i2.hdslb.com/c.jpg"),
                         "https://i2.hdslb.com/c.jpg")
        self.assertEqual(normalize_cover_url(""), "")

    def test_http_rate_limit_statuses_are_retryable(self):
        for status in (403, 412, 429):
            kind, _ = classify_view_response(status, None)
            self.assertEqual(kind, CoverStatus.RATE_LIMITED)

    def test_source_side_permanent_codes_are_not_retryable(self):
        for code in (62002, 62012, -404):
            kind, _ = classify_view_response(200, {"code": code})
            self.assertEqual(kind, CoverStatus.PERMANENT)

    def test_successful_response_yields_normalized_evidence(self):
        kind, evidence = classify_view_response(200, {
            "code": 0,
            "data": {
                "pic": "//i0.hdslb.com/bfs/archive/x.jpg",
                "title": "标题",
                "owner": {"name": "UP"},
                "duration": 120,
                "stat": {"view": 42},
            },
        })
        self.assertEqual(kind, CoverStatus.OK)
        self.assertEqual(evidence["cover"],
                         "https://i0.hdslb.com/bfs/archive/x.jpg")
        self.assertEqual(evidence["upzhu"], "UP")
        self.assertEqual(evidence["view"], 42)

    def test_network_error_is_a_plain_failure(self):
        kind, _ = classify_view_response(None, None, RuntimeError("timeout"))
        self.assertEqual(kind, CoverStatus.FAIL)

    def test_retry_gap_grows_each_round(self):
        policy = FetchPolicy(retry_base_gap=0.8, retry_step=0.6)
        self.assertAlmostEqual(policy.retry_gap(1), 1.4)
        self.assertAlmostEqual(policy.retry_gap(2), 2.0)

    def test_fetch_covers_retries_only_rate_limited_rows(self):
        attempts = {}

        def fetch(record):
            key = record["bvid"]
            attempts[key] = attempts.get(key, 0) + 1
            if key == "BV_RL" and attempts[key] == 1:
                return 429, None, None
            if key == "BV_GONE":
                return 200, {"code": 62002}, None
            return 200, {"code": 0, "data": {"pic": "//i0.hdslb.com/" + key}}, None

        policy = FetchPolicy(concurrency=4, batch_gap=0,
                             retry_concurrency=2, retry_base_gap=0,
                             retry_step=0, max_rounds=2, sleeper=lambda _: None)
        result = fetch_covers(
            [{"bvid": "BV_A"}, {"bvid": "BV_RL"}, {"bvid": "BV_GONE"}],
            fetch, policy=policy)
        self.assertEqual(len(result["records"]), 2)
        self.assertEqual(attempts["BV_RL"], 2)
        self.assertEqual(result["stats"]["rate_limited"], 1)
        self.assertEqual(result["stats"]["ok"], 2)
        self.assertEqual(result["pending"], [])

    def test_fetch_covers_keeps_permanent_and_pending_separate(self):
        calls = []

        def fetch(record):
            calls.append(record["bvid"])
            if record["bvid"] == "BV_GONE":
                return 200, {"code": 62002}, None
            return 429, None, None

        policy = FetchPolicy(concurrency=3, batch_gap=0,
                             retry_concurrency=2, retry_base_gap=0,
                             retry_step=0, max_rounds=1, sleeper=lambda _: None)
        result = fetch_covers(
            [{"bvid": "BV_GONE"}, {"bvid": "BV_RL1"}, {"bvid": "BV_RL2"}],
            fetch, policy=policy)
        self.assertEqual([r["bvid"] for r in result["permanent"]], ["BV_GONE"])
        self.assertEqual(len(result["pending"]), 2)
        self.assertEqual(result["stats"]["permanent"], 1)
        self.assertNotIn("BV_GONE", calls[3:])

    def test_fetch_policy_rejects_invalid_values(self):
        with self.assertRaises(ValueError):
            FetchPolicy(concurrency=0)
        with self.assertRaises(ValueError):
            FetchPolicy(batch_gap=-1)


if __name__ == "__main__":
    unittest.main()
