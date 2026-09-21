# Bilibili Subtitle Fetch Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the repository's actually-present subtitle workflows portable and honest enough for another agent to run without inheriting this machine's paths, browser profile, or undocumented files.

**Architecture:** Keep the current scripts and Selenium/Playwright approach. Add one tiny runtime-path helper shared by the scripts, make machine-specific browser paths environment-configurable, and remove the documented-but-missing batch rescue feature from the first deliverable. Do not introduce a framework, service, or new persistence layer.

**Tech Stack:** Python 3.7+, Selenium, Playwright, yt-dlp, faster-whisper, ffmpeg, `unittest`.

**Spec:** Current [`SKILL.md`](../../../SKILL.md), [`README.md`](../../../README.md), and the observed runtime contract of the scripts.

## Global Constraints

- Keep Python 3.7 compatibility for the base workflow; do not add syntax that requires Python 3.10.
- Do not kill every Chrome/Edge process by default; closing a user's unrelated sessions is not an acceptable hidden side effect.
- Do not add a batch-rescue implementation in this pass; remove its false documentation instead.
- No new third-party dependency for path handling, CLI parsing, tests, or UTF-8 output.
- Every changed behavior gets one runnable `unittest` or CLI smoke check.

---

### Task 1: Add portable runtime paths

**Files:**
- Create: `scripts/runtime_paths.py`
- Modify: `scripts/subtitle_extractor.py:15-25,565`
- Modify: `scripts/fetch_search_bvids.py:26-30`
- Modify: `scripts/run_subtitle_batch.py:28-35`
- Modify: `scripts/rescue_one_subtitle.py:22-24,194-197`
- Test: `tests/test_runtime_paths.py`

**Interfaces:**
- `runtime_paths.project_root() -> Path`: return `BILIBILI_SFETCH_ROOT` when set; otherwise walk parent directories for `.git`; otherwise use the repository directory containing `scripts`.
- `runtime_paths.default_output_dir() -> Path`: return `project_root() / "10_raw" / "01_B站视频转录"`.
- `runtime_paths.edge_profile_dir() -> Path`: return `BILIBILI_SFETCH_EDGE_PROFILE` when set; otherwise use `%LOCALAPPDATA%/Microsoft/Edge/User Data`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_runtime_paths.py
import os
import unittest
from pathlib import Path

from scripts import runtime_paths


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


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test and verify it fails**

Run: `python -m unittest tests.test_runtime_paths -v`

Expected: FAIL because `scripts/runtime_paths.py` does not exist.

- [ ] **Step 3: Implement the smallest helper**

Use `Path(__file__).resolve().parents[1]` as the standalone-repository fallback. Walk parents only to support a skill installed inside a Git-backed vault; do not use fixed parent counts.

- [ ] **Step 4: Replace each duplicated path calculation**

Use the helper for default output, compile DB, favorites input, rescue output, and Edge profile. Keep explicit `--output` and constructor arguments higher priority than defaults.

- [ ] **Step 5: Run the test and smoke-import the scripts**

Run: `python -m unittest tests.test_runtime_paths -v`

Expected: PASS.

Run: `python -c "import sys; sys.path.insert(0, 'scripts'); import subtitle_extractor, fetch_search_bvids, run_subtitle_batch; print(subtitle_extractor.SUBTITLE_DIR); print(fetch_search_bvids.DEFAULT_OUTPUT_DIR); print(run_subtitle_batch.DEFAULT_OUTPUT_DIR)"`

Expected: every default output path starts under `E:\002-Pr\bilibili-subtitle-fetch`.

- [ ] **Step 6: Commit**

```bash
git add scripts/runtime_paths.py scripts/subtitle_extractor.py scripts/fetch_search_bvids.py scripts/run_subtitle_batch.py scripts/rescue_one_subtitle.py tests/test_runtime_paths.py
git commit -m "fix: make subtitle tool paths portable"
```

### Task 2: Remove machine-specific driver and profile assumptions

**Files:**
- Modify: `scripts/_driver_patch.py:30-39,42-86`
- Modify: `scripts/rescue_one_subtitle.py:15,96-102`
- Modify: `scripts/subtitle_extractor.py:222-251`
- Test: `tests/test_driver_config.py`

**Interfaces:**
- Environment variables remain the override interface: `BILIBILI_SFETCH_CHROMEDRIVER`, `BILIBILI_SFETCH_CHROME_BIN`, and `BILIBILI_SFETCH_EDGE_PROFILE`.
- If the driver patch cannot resolve a real executable, fail with one actionable error before starting a batch; do not continue with a guaranteed driver failure.

- [ ] **Step 1: Write tests for portable configuration**

```python
def test_driver_defaults_are_not_machine_user_paths():
    import scripts._driver_patch as patch
    assert "C:\\Users\\HP" not in patch.DEFAULT_CHROMEDRIVER
    assert "C:\\Users\\HP" not in patch.DEFAULT_CHROME_BIN
```

- [ ] **Step 2: Replace constants with discovery**

Resolve executable paths in this order: environment variable, `shutil.which`, then a clear missing-path error. Do not embed a username or a browser version in source.

- [ ] **Step 3: Use the shared profile resolver**

Replace the hardcoded Edge profile with `edge_profile_dir()`. Keep the profile path configurable because another agent may use a different Windows account or a non-default profile.

- [ ] **Step 4: Make browser shutdown explicit**

Remove unconditional `taskkill /F /IM chrome.exe` and `taskkill /F /IM msedge.exe`. Add an opt-in `--close-browser` flag only if the existing profile-lock workaround is still required; default execution must leave unrelated browser sessions alive.

- [ ] **Step 5: Run checks**

Run: `python -m unittest tests.test_driver_config -v`

Expected: PASS.

Run: `python -m py_compile scripts/*.py` (PowerShell: `Get-ChildItem scripts/*.py | % { python -m py_compile $_.FullName }`).

Expected: PASS with no syntax errors.

- [ ] **Step 6: Commit**

```bash
git add scripts/_driver_patch.py scripts/rescue_one_subtitle.py scripts/subtitle_extractor.py tests/test_driver_config.py
git commit -m "fix: remove machine-specific browser assumptions"
```

### Task 3: Make every entry point safe and self-describing

**Files:**
- Modify: `scripts/subtitle_extractor.py:604-614`
- Modify: `scripts/fetch_search_bvids.py:83-92`
- Modify: `scripts/run_subtitle_batch.py:67-75`
- Modify: `scripts/extract_meta.py` and `scripts/prepend_meta.py` only if the shared UTF-8 setup is moved there
- Test: `tests/test_cli_smoke.py`

**Interfaces:**
- All executable scripts accept `-h/--help` without starting a browser.
- User-facing Windows output is UTF-8 with replacement for unrepresentable console characters.
- Search `target` is a positive integer; invalid values exit with a short parser error.

- [ ] **Step 1: Add CLI smoke tests**

```python
import subprocess
import sys
import unittest


class CliSmokeTest(unittest.TestCase):
    def test_subtitle_help(self):
        p = subprocess.run([sys.executable, "scripts/subtitle_extractor.py", "--help"], text=True, capture_output=True)
        self.assertEqual(p.returncode, 0)
        self.assertIn("--output", p.stdout)

    def test_search_help_does_not_launch_browser(self):
        p = subprocess.run([sys.executable, "scripts/fetch_search_bvids.py", "--help"], text=True, capture_output=True)
        self.assertEqual(p.returncode, 0)
        self.assertIn("keyword", p.stdout)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Add argparse to search and batch scripts**

Replace positional `sys.argv` parsing with `argparse`; keep the existing positional forms and defaults so documented commands continue to work.

- [ ] **Step 3: Configure UTF-8 before parser help is printed**

Use `sys.stdout.reconfigure(encoding="utf-8", errors="replace")` and the same for stderr when available. Do not rely on emoji being encodable by the Windows GBK console.

- [ ] **Step 4: Remove or implement the unused batch timeout honestly**

For this minimal release, remove `PER_VIDEO_TIMEOUT` and the implication that it is enforced. Do not leave a false timeout constant. A real hard timeout requires running each video in a child process and is a separate change.

- [ ] **Step 5: Run checks**

Run: `python -m unittest tests.test_cli_smoke -v`

Expected: PASS; no browser window opens.

Run: `python scripts/run_subtitle_batch.py --help`

Expected: PASS with usage text and no filesystem/network side effects.

- [ ] **Step 6: Commit**

```bash
git add scripts/subtitle_extractor.py scripts/fetch_search_bvids.py scripts/run_subtitle_batch.py tests/test_cli_smoke.py
git commit -m "fix: make subtitle CLIs safe to invoke"
```

### Task 4: Align the rescue feature with the code that actually exists

**Files:**
- Modify: `SKILL.md:1-18,135-150,265-275,360-380`
- Modify: `README.md:1-220`
- Modify: `scripts/rescue_one_subtitle.py:175-220`
- Test: `tests/test_rescue_contract.py`

**Interfaces:**
- The first deliverable exposes single-video rescue only: `python scripts/rescue_one_subtitle.py BV...`.
- Documentation must not mention `rescue_failed_subtitles.py` until that file exists.
- A successful single rescue writes/updates the same `download_list.json` contract used by `SubtitleExtractor`.

- [ ] **Step 1: Write the failing contract test**

```python
from pathlib import Path
import unittest


class RescueContractTest(unittest.TestCase):
    def test_batch_rescue_is_not_advertised_without_an_implementation(self):
        skill = Path("SKILL.md").read_text(encoding="utf-8")
        self.assertNotIn("rescue_failed_subtitles.py", skill)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Decide the minimal scope**

Remove batch-rescue commands and claims from both docs. Keep the single-rescue command and state that failed-record batch processing is not included.

- [ ] **Step 3: Update the download list after single rescue**

Reuse the existing JSON shape: `records[BV] = {status, downloaded_at, subtitle_path}` on success, and `{status, downloaded_at, error}` on failure. Add a small helper in `rescue_one_subtitle.py` rather than importing the whole Selenium extractor.

- [ ] **Step 4: Clean up temporary audio on failure**

Wrap the m4s/mp3 lifecycle in `try/finally`; retain the current `--keep-audio` behavior for debugging.

- [ ] **Step 5: Run checks**

Run: `python -m unittest tests.test_rescue_contract -v`

Expected: PASS.

Run: `python -m py_compile scripts/rescue_one_subtitle.py`.

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add SKILL.md README.md scripts/rescue_one_subtitle.py tests/test_rescue_contract.py
git commit -m "fix: align rescue docs and status tracking"
```

### Task 5: Correct dependencies and delivery state

**Files:**
- Modify: `README.md` dependency sections
- Modify: `SKILL.md` dependency and command examples
- Modify: `.gitignore` only if generated graph artifacts should remain local
- Test: `tests/test_delivery_manifest.py`

**Interfaces:**
- Base mode: Selenium plus a working Chrome/Edge browser and driver strategy.
- Space mode: `yt-dlp`.
- Search mode: Playwright; the selected browser channel must exist.
- Rescue mode: Playwright, faster-whisper, ffmpeg, and a logged-in Edge profile.

- [ ] **Step 1: Add a delivery manifest test**

```python
from pathlib import Path
import unittest


class DeliveryManifestTest(unittest.TestCase):
    def test_documented_scripts_exist(self):
        expected = {
            "scripts/subtitle_extractor.py",
            "scripts/extract_meta.py",
            "scripts/prepend_meta.py",
            "scripts/fetch_search_bvids.py",
            "scripts/run_subtitle_batch.py",
            "scripts/_driver_patch.py",
            "scripts/rescue_one_subtitle.py",
        }
        for path in expected:
            self.assertTrue(Path(path).exists(), path)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Update dependency instructions**

Document `yt-dlp` for `--space`, Playwright for search/rescue, faster-whisper and ffmpeg for rescue, and the environment variables for non-default browser/driver locations.

- [ ] **Step 3: Normalize examples**

Use repository-relative commands such as `python scripts/...`; remove stale `.claude/skills/...` and unrelated vault wrapper paths from the repository README.

- [ ] **Step 4: Keep generated graph output out of delivery**

Do not commit `graphify-out/` unless the project explicitly wants the graph artifact. Remove it or add it to `.gitignore` before handoff.

- [ ] **Step 5: Run the complete gate**

Run:

```bash
python -m unittest discover -s tests -v
python -m compileall -q scripts
git diff --check
git status --short
```

Expected: all tests pass, compilation is clean, and every intended new script is tracked.

- [ ] **Step 6: Commit the delivery**

```bash
git add .gitignore README.md SKILL.md scripts tests
git commit -m "chore: prepare subtitle fetch for agent handoff"
```

## Acceptance Gate

The repository is ready for another agent only when all of these are true:

- `python scripts/subtitle_extractor.py --help` exits 0 on Windows.
- `python scripts/fetch_search_bvids.py --help` exits 0 without opening a browser.
- Default paths resolve under the repository or an explicit `BILIBILI_SFETCH_ROOT`.
- No source file contains the current user's name or a fixed browser version.
- The docs mention only scripts that exist.
- Single rescue updates `download_list.json` or the docs explicitly mark status tracking as unsupported.
- `python -m unittest discover -s tests -v` passes.
- All handoff files are tracked in Git.

## Deferred Work

- Implementing `rescue_failed_subtitles.py` as a real batch feature.
- Enforcing per-video hard timeouts with child-process isolation.
- Refactoring the large `SubtitleExtractor` class into multiple modules.
- Adding live B 站 integration tests; those need user login, browser state, and network access.
