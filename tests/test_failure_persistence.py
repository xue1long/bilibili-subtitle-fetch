import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

from bili_guard import FailureKind, SessionStatus  # noqa: E402
from run_subtitle_batch import run_one  # noqa: E402
from subtitle_extractor import SubtitleExtractor  # noqa: E402


class FailurePersistenceTest(unittest.TestCase):
    def test_legacy_download_record_remains_readable_and_new_records_get_defaults(self):
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            (output_dir / "download_list.json").write_text(
                json.dumps({
                    "records": {
                        "BVLEGACY": {
                            "status": "success",
                            "subtitle_path": "BVLEGACY.md",
                        }
                    }
                }),
                encoding="utf-8",
            )

            extractor = SubtitleExtractor(output_dir=output_dir, enrich_with_meta=False)

            self.assertEqual(extractor.extract_single("BVLEGACY"), "SKIPPED")
            self.assertEqual(
                extractor._download_list["records"]["BVLEGACY"]["status"],
                "success",
            )
            extractor._update_download_list("BVNEW", "failed")
            record = extractor._download_list["records"]["BVNEW"]
            self.assertEqual(record["probe"], {
                "request_observed": False,
                "response_status": None,
                "payload_received": False,
                "subtitle_count": 0,
                "language_count": 0,
                "hook_installed": False,
                "page_ready": False,
                "button_found": False,
                "click_dispatched": False,
                "request_count": 0,
            })
            self.assertEqual(record["duration_sec"], 0.0)

    def test_rate_limit_failure_is_persisted(self):
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            extractor = SubtitleExtractor(output_dir=output_dir, enrich_with_meta=False)
            extractor._update_download_list(
                "BVTEST123",
                "paused",
                error="HTTP 412",
                error_code=FailureKind.RATE_LIMITED,
                retry_after="2099-01-01T00:00:00Z",
            )
            record = json.loads(
                (output_dir / "download_list.json").read_text(encoding="utf-8")
            )["records"]["BVTEST123"]
            self.assertEqual(record["error_code"], FailureKind.RATE_LIMITED)
            self.assertEqual(record["retry_after"], "2099-01-01T00:00:00Z")

    def test_failure_persists_compact_probe_and_duration(self):
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            extractor = SubtitleExtractor(output_dir=output_dir, enrich_with_meta=False)
            extractor._update_download_list(
                "BVTEST123", "failed", error="not observed",
                error_code=FailureKind.SUBTITLE_NOT_OBSERVED,
                probe={"request_observed": False, "response_url": "secret"},
                duration_sec=1.25,
            )
            record = json.loads(
                (output_dir / "download_list.json").read_text(encoding="utf-8")
            )["records"]["BVTEST123"]
            self.assertEqual(record["duration_sec"], 1.25)
            self.assertEqual(record["probe"], {
                "request_observed": False,
                "response_status": None,
                "payload_received": False,
                "subtitle_count": 0,
                "language_count": 0,
                "hook_installed": False,
                "page_ready": False,
                "button_found": False,
                "click_dispatched": False,
                "request_count": 0,
            })
            self.assertNotIn("response_url", record["probe"])

    def test_circuit_open_record_has_default_probe_and_duration(self):
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            extractor = SubtitleExtractor(
                output_dir=output_dir, enrich_with_meta=False,
                rate_limit_threshold=1,
            )
            extractor.breaker.record(FailureKind.RATE_LIMITED)
            self.assertIsNone(extractor.extract_single("BVTEST123"))
            record = json.loads(
                (output_dir / "download_list.json").read_text(encoding="utf-8")
            )["records"]["BVTEST123"]
            self.assertIn("probe", record)
            self.assertIn("duration_sec", record)

    def test_run_one_returns_exact_triple(self):
        class FakeExtractor:
            last_failure_kind = FailureKind.RATE_LIMITED

            def extract_single(self, bvid):
                return None

        result = run_one(FakeExtractor(), "BVTEST123")
        self.assertEqual(len(result), 3)
        self.assertEqual(result[2], FailureKind.RATE_LIMITED)

    def test_batch_stops_after_pause_result(self):
        import run_subtitle_batch

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bvids_path = root / "bvids.json"
            bvids_path.write_text(
                json.dumps({
                    "keyword": "test",
                    "videos": [
                        {"bvid": "BV1", "title": "one"},
                        {"bvid": "BV2", "title": "two"},
                        {"bvid": "BV3", "title": "three"},
                    ],
                }),
                encoding="utf-8",
            )
            fake_extractor = type("FakeExtractor", (), {})()
            fake_extractor.breaker = type("FakeBreaker", (), {"state": "CLOSED"})()
            fake_module = type(
                "FakeModule",
                (), {"SubtitleExtractor": lambda **kwargs: fake_extractor},
            )
            calls = []

            def fake_run_one(extractor, bvid):
                calls.append(bvid)
                if len(calls) == 2:
                    fake_extractor.breaker.state = "OPEN"
                return "failed", "HTTP 412", FailureKind.RATE_LIMITED

            with patch.object(run_subtitle_batch, "patch_selenium_edge", return_value=False), \
                    patch.object(run_subtitle_batch, "load_skill_module", return_value=fake_module), \
                    patch.object(run_subtitle_batch, "run_one", side_effect=fake_run_one) as run_one_mock, \
                    patch.object(sys, "argv", [
                        "run_subtitle_batch.py", str(bvids_path), str(root), "edge",
                    ]):
                exit_code = run_subtitle_batch.main()

            self.assertEqual(exit_code, 10)
            self.assertEqual(run_one_mock.call_count, 2)
            summary = json.loads(
                (root / "test_results.json").read_text(encoding="utf-8")
            )
            self.assertEqual(summary["pause_reason"], FailureKind.RATE_LIMITED)

    def test_exception_after_observed_rate_limit_preserves_probe_precedence(self):
        with tempfile.TemporaryDirectory() as directory:
            extractor = SubtitleExtractor(
                output_dir=Path(directory), enrich_with_meta=False, browser="edge"
            )
            driver = type("Driver", (), {
                "get": lambda self, url: None,
                "execute_script": lambda self, script: (_ for _ in ()).throw(RuntimeError("later failure")),
                "quit": lambda self: None,
            })()
            observed = {
                "request_observed": True,
                "response_status": 412,
                "payload_received": False,
            }

            with patch("selenium.webdriver.Edge", return_value=driver), \
                    patch("subtitle_extractor.check_login_state", return_value=SessionStatus(True)), \
                    patch("subtitle_extractor.install_subtitle_probe", side_effect=lambda _: setattr(extractor, "last_probe", observed)), \
                    patch("subtitle_extractor.classify_failure", return_value=FailureKind.RATE_LIMITED) as classify:
                extractor._started_at = 0
                extractor._extract_single_once("BVTEST123")

            self.assertEqual(classify.call_args.kwargs["subtitle_probe"], observed)

    def test_exception_refreshes_probe_when_installer_did_not_update_extractor(self):
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            extractor = SubtitleExtractor(
                output_dir=output_dir, enrich_with_meta=False, browser="edge"
            )
            observed = {
                "request_observed": True,
                "response_status": 412,
                "payload_received": False,
            }

            class Driver:
                def get(self, url):
                    pass

                def execute_script(self, script):
                    if "window.__subtitleProbe" in script:
                        return observed
                    raise RuntimeError("later failure")

                def quit(self):
                    pass

            with patch("selenium.webdriver.Edge", return_value=Driver()), \
                    patch("subtitle_extractor.check_login_state", return_value=SessionStatus(True)), \
                    patch("subtitle_extractor.install_subtitle_probe"), \
                    patch("subtitle_extractor.time.sleep"):
                extractor._extract_single_once("BVTEST123")

            self.assertEqual(extractor.last_failure_kind, FailureKind.RATE_LIMITED)
            record = extractor._download_list["records"]["BVTEST123"]
            self.assertEqual(record["error_code"], FailureKind.RATE_LIMITED)

    def test_skipped_video_resets_previous_failure_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            extractor = SubtitleExtractor(output_dir=Path(directory), enrich_with_meta=False)
            extractor._download_list["records"]["BVTEST123"] = {"status": "success"}
            extractor.last_failure_kind = FailureKind.RATE_LIMITED
            extractor.last_failure_message = "old"
            extractor.last_probe = {"request_observed": True, "response_status": 412}
            extractor.last_duration_sec = 9.5

            self.assertEqual(extractor.extract_single("BVTEST123"), "SKIPPED")
            self.assertIsNone(extractor.last_failure_kind)
            self.assertEqual(extractor.last_failure_message, "")
            self.assertEqual(extractor.last_duration_sec, 0.0)
            self.assertEqual(extractor.last_probe["response_status"], None)


if __name__ == "__main__":
    unittest.main()
