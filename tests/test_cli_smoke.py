import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import run_subtitle_batch  # noqa: E402
import fetch_search_bvids  # noqa: E402


class CliSmokeTest(unittest.TestCase):
    def test_project_defaults_to_chrome(self):
        self.assertEqual(run_subtitle_batch.DEFAULT_BROWSER, "chrome")
        self.assertEqual(fetch_search_bvids.DEFAULT_BROWSER, "chrome")

    def run_help(self, script):
        return subprocess.run(
            [sys.executable, str(ROOT / script), "--help"],
            cwd=ROOT,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=15,
        )

    def test_subtitle_help(self):
        result = self.run_help("scripts/subtitle_extractor.py")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--output", result.stdout)
        for option in (
            "--min-delay",
            "--max-delay",
            "--rate-limit-threshold",
            "--cooldown-seconds",
        ):
            self.assertIn(option, result.stdout)

    def test_search_help_does_not_launch_browser(self):
        result = self.run_help("scripts/fetch_search_bvids.py")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("keyword", result.stdout)

    def test_batch_help_does_not_read_default_input(self):
        result = self.run_help("scripts/run_subtitle_batch.py")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("bvids_json", result.stdout)
        self.assertIn("--cooldown-seconds", result.stdout)

    def test_rescue_help_does_not_load_asr_model(self):
        result = self.run_help("scripts/rescue_one_subtitle.py")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--model", result.stdout)

    def test_batch_cli_persists_complete_output_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bvids = root / "bvids.json"
            bvids.write_text(json.dumps({
                "keyword": "contract",
                "videos": [
                    {"bvid": "BV1", "title": "Card title", "title_source": "card"},
                    {"bvid": "BV2", "title": "", "title_source": "unresolved"},
                    {"bvid": "BV3", "title": "Skipped title", "title_source": "detail"},
                ],
            }), encoding="utf-8")

            class FakeExtractor:
                def __init__(self, **kwargs):
                    self.calls = 0
                    self.last_probe = {}
                    self.last_duration_sec = 0.25
                    self.browser_start_count = 1
                    self.browser_restart_count = 0
                    self.breaker = type("Breaker", (), {"state": "CLOSED"})()
                    self.closed = False

                def extract_single(self, bvid):
                    self.calls += 1
                    if bvid == "BV3":
                        del self.last_probe
                        del self.last_duration_sec
                        return "SKIPPED"
                    self.last_probe = {"request_observed": bvid == "BV2"}
                    self.last_duration_sec = 0.5 if bvid == "BV1" else 0.75
                    if bvid == "BV2":
                        self.last_failure_kind = "SUBTITLE_NOT_OBSERVED"
                        return None
                    return "saved.md"

                def close(self):
                    self.closed = True

            fake_extractor = None

            class FakeModule:
                def SubtitleExtractor(self, **kwargs):
                    nonlocal fake_extractor
                    fake_extractor = FakeExtractor(**kwargs)
                    return fake_extractor

            with patch.object(run_subtitle_batch, "patch_selenium_edge", return_value=False), \
                    patch.object(run_subtitle_batch, "load_skill_module", return_value=FakeModule()), \
                    patch.object(sys, "argv", ["run_subtitle_batch.py", str(bvids), str(root), "edge"]):
                self.assertEqual(run_subtitle_batch.main(), 2)

            results = json.loads((root / "contract_results.json").read_text(encoding="utf-8"))
            probe_fields = {
                "request_observed", "response_status", "payload_received", "subtitle_count",
                "language_count", "hook_installed", "page_ready", "button_found",
                "click_dispatched", "request_count",
            }
            records = results["success"] + results["skipped"] + results["failed"]
            self.assertEqual([record["title_source"] for record in records], ["card", "detail", "unresolved"])
            for record in records:
                self.assertEqual(set(record["probe"]), probe_fields)
                self.assertIsInstance(record["duration_sec"], float)
            self.assertEqual(results["failed"][0]["error_code"], "SUBTITLE_NOT_OBSERVED")
            self.assertEqual(results["skipped"][0]["bvid"], "BV3")
            self.assertTrue(fake_extractor.closed)

    def test_batch_cli_closes_extractor_when_result_write_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bvids = root / "bvids.json"
            bvids.write_text(json.dumps({
                "keyword": "close",
                "videos": [{"bvid": "BV1", "title": "title", "title_source": "card"}],
            }), encoding="utf-8")

            class FakeExtractor:
                last_probe = {}
                last_duration_sec = 0.1
                browser_start_count = 1
                browser_restart_count = 0
                breaker = type("Breaker", (), {"state": "CLOSED"})()

                def extract_single(self, bvid):
                    return "saved.md"

                def close(self):
                    self.closed = True

            fake_extractor = FakeExtractor()
            fake_module = type("FakeModule", (), {
                "SubtitleExtractor": lambda self, **kwargs: fake_extractor,
            })()
            with patch.object(run_subtitle_batch, "patch_selenium_edge", return_value=False), \
                    patch.object(run_subtitle_batch, "load_skill_module", return_value=fake_module), \
                    patch.object(run_subtitle_batch.json, "dump", side_effect=RuntimeError("write failed")), \
                    patch.object(sys, "argv", ["run_subtitle_batch.py", str(bvids), str(root), "edge"]):
                with self.assertRaisesRegex(RuntimeError, "write failed"):
                    run_subtitle_batch.main()
            self.assertTrue(fake_extractor.closed)


if __name__ == "__main__":
    unittest.main()
