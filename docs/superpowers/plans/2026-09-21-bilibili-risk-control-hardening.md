# Bilibili Risk-Control Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Bilibili subtitle jobs detect invalid login state, reduce request pressure, and stop safely when Bilibili begins rate-limiting instead of retrying into a longer block.

**Architecture:** Add one small, dependency-free guard module containing positive-signal session checks, browser response probing, a single-process rate limiter, and a persisted circuit breaker. Integrate it at `SubtitleExtractor.extract_single()`, because favorites, space, and batch workflows already converge there; use the same guard contract in search and rescue entry points. Persist only control state and failure codes, never cookies or raw page data. The browser-session path remains the primary path; direct `extract_meta.py` HTTP access remains a best-effort standalone command and must report HTTP 412/429 as a non-success.

**Tech Stack:** Python 3.7+, Selenium, Playwright, `unittest`; no new third-party dependency.

**Spec:** Existing project behavior in [`SKILL.md`](../../../SKILL.md), [`README.md`](../../../README.md), and the observed 412 behavior documented in the current task.

## Global Constraints

- Use the existing logged-in browser profile; never print, export, or persist cookie values. A page containing `window.__INITIAL_STATE__` is not considered proof of login.
- Keep concurrency at one browser/video job per process.
- Default pacing is 8–15 seconds between videos with random jitter; all values remain CLI-configurable.
- A login failure stops immediately; it is not retried. Mid-run loss of login uses the same stop path.
- Two consecutive observed rate-limit events open the circuit; the default cooldown is 15 minutes. The state survives process restart and is checked before the next job.
- Browser retries belong to the guard: at most one retry for browser/page startup failures, zero immediate retries for login or rate limiting. `extract_single()` must not recursively retry.
- Do not add proxy rotation, fingerprint spoofing, CAPTCHA solving, multi-account rotation, or parallel scraping.
- Preserve the existing `download_list.json` resume behavior: successful videos remain skippable after a paused run.
- Keep Python 3.7 compatibility and use `unittest`.

## Review Focus

- A logged-out profile must stop before attempting subtitle extraction and tell the user to log in. Login is established by a positive account-surface signal plus no login CTA; initial-state presence alone is insufficient.
- Browser-side subtitle responses must expose status and payload evidence to Python. A 412/429 is `RATE_LIMITED`; a 200 with no subtitle is `NO_SUBTITLE`; no status with no payload is `SUBTITLE_API_FAILED`.
- A video with no AI subtitle must not open the circuit or stop the whole batch.
- Two 412/429-style events must pause the batch and record `RATE_LIMITED`, not perform another immediate retry.
- A browser startup crash may retry once, but repeated startup failure must not loop forever.
- A cooldown must use monotonic time in-process and an UTC wall-clock `retry_after` across restarts; a backward clock jump fails closed.
- Favorites, space, batch, search, and rescue must use the same failure codes, pause behavior, and persisted state.

## Current Call Graph

```text
extract_favorites() ─┐
extract_space() ─────┼─> SubtitleExtractor.extract_single()
run_subtitle_batch ──┘             │
                                   ├─ browser/profile/session
                                   ├─ login preflight + subtitle response probe
                                   ├─ metadata from window.__INITIAL_STATE__
                                   ├─ persisted .bili_guard_state.json
                                   └─ download_list.json
```

The shared integration point is [`scripts/subtitle_extractor.py`](../../../scripts/subtitle_extractor.py:205). Search collection has its own persistent Playwright profile in [`scripts/fetch_search_bvids.py`](../../../scripts/fetch_search_bvids.py:111); the same guard rules must apply to the later subtitle batch, not only to search.

## Planned Interfaces

Create `scripts/bili_guard.py` with these small interfaces:

```python
class FailureKind:
    LOGIN_REQUIRED = "LOGIN_REQUIRED"
    RATE_LIMITED = "RATE_LIMITED"
    NO_SUBTITLE = "NO_SUBTITLE"
    BROWSER_START_FAILED = "BROWSER_START_FAILED"
    PAGE_LOAD_FAILED = "PAGE_LOAD_FAILED"
    SUBTITLE_API_FAILED = "SUBTITLE_API_FAILED"
    UNKNOWN = "UNKNOWN"


class SessionStatus:
    def __init__(self, ok: bool, reason: str = ""):
        self.ok = ok
        self.reason = reason


class RateLimiter:
    def __init__(self, min_delay=8.0, max_delay=15.0, clock=None, sleeper=None, rng=None):
        pass
    def wait(self):
        pass


class CircuitBreaker:
    def __init__(self, state_path, threshold=2, cooldown_seconds=900,
                 clock=None, wall_clock=None):
        pass
    def allow(self) -> bool:
        pass
    def record(self, kind: str) -> None:
        pass
    def reset(self) -> None:
        pass
    @property
    def state(self) -> str:
        pass
    @property
    def retry_after(self):
        pass


def check_login_state(driver) -> SessionStatus:
    pass


def install_subtitle_probe(driver) -> None:
    pass


def read_subtitle_probe(driver):
    pass


def classify_failure(error=None, response_status=None, session=None,
                     subtitle_probe=None) -> str:
    pass
```

`clock`, `wall_clock`, `sleeper`, and `rng` are optional test seams; production defaults use `time.monotonic`, `datetime.now(timezone.utc)`, `time.sleep`, and `random.uniform`. `check_login_state` makes no network request. It checks a positive account surface (for example, avatar/account menu) and rejects an explicit login CTA; `window.__INITIAL_STATE__` is page-loaded evidence only. If the driver cannot provide a URL/body because the page did not load, return `PAGE_LOAD_FAILED`, not `LOGIN_REQUIRED`.

`install_subtitle_probe` must observe the matching subtitle fetch/XHR status and compact payload facts. `read_subtitle_probe` returns only this shape:

```json
{
  "response_status": 200,
  "response_url": "https://api.bilibili.com/...",
  "payload_received": true,
  "subtitle_count": 87,
  "language_count": 1
}
```

The implementation must not persist response bodies, cookies, authorization headers, page text, or raw response URLs. `response_url` is an in-memory diagnostic field only; if logged, strip its query string. `RATE_LIMITED` requires an observed 412/429 or an explicit platform rate-limit signal; a 200 with no payload is `NO_SUBTITLE`, while no status with no payload is `SUBTITLE_API_FAILED`.

`CircuitBreaker` persists only control state in `.bili_guard_state.json` beside the output directory, using an atomic temporary-file replace:

```json
{
  "version": 1,
  "state": "OPEN",
  "consecutive_rate_limits": 2,
  "reason": "RATE_LIMITED",
  "opened_at": "2026-09-21T08:00:00Z",
  "retry_after": "2026-09-21T08:15:00Z"
}
```

On restart, `retry_after` is authoritative. A backward wall-clock jump fails closed and requires operator review; an in-process cooldown still uses monotonic time.

## P0/P1 Review Corrections

The following decisions are mandatory acceptance conditions for the original plan:

| Risk | Correction in this plan |
|---|---|
| P0: Browser 412/429 may never reach Python | Install a browser-side probe for the matching subtitle fetch/XHR and classify from its status plus compact payload facts. No generic exception-only detection. |
| P0: Initial-state presence is mistaken for login | Require positive account-surface evidence and reject login CTAs. Test logged-out and logged-in page fixtures. |
| P0: Restart bypasses an in-memory breaker | Persist `.bili_guard_state.json` atomically and enforce `retry_after` before every job. |
| P0: Missing subtitle, API failure, and rate limit are conflated | Use the explicit `response_status`/`payload_received` matrix in `classify_failure`. |
| P0: Search/rescue paths can bypass the guard | Route subtitle extraction, rescue, and batch through the same guard; perform search login preflight and share the same state directory. |
| P1: Retry ownership can recurse | Remove recursive retry from `extract_single`; the guard owns exactly one browser/page-start retry and no rate-limit retry. |
| P1: Batch result contract is ambiguous | `run_one()` returns the exact triple `(status, message, error_code)` and the batch maps pause errors to exit codes 10/11/12. |
| P1: Restart and resume behavior is unspecified | Successful records remain skippable, unvisited records remain pending, and persisted `retry_after` is tested across a new guard instance. |
| P1: Shared result writes can race | Keep the supported execution model single-process and protect download-list read/modify/write with one process lock. |
| P1: Thresholds are arbitrary and unauditable | Persist threshold/cooldown inputs in the run summary and treat them as CLI-configurable operator policy, not hidden constants. |

The implementation is intentionally single-process. Distributed locking, proxy rotation, CAPTCHA handling, and adaptive account pools remain out of scope.

---

### Task 1: Add failure classification and login-state detection

**Files:**
- Create: `scripts/bili_guard.py`
- Modify: `scripts/subtitle_extractor.py:258-380`
- Test: `tests/test_bili_guard.py`

**Interfaces:**
- `check_login_state(driver) -> SessionStatus` reads current URL, page text, and positive account-surface markers; `window.__INITIAL_STATE__` can only confirm that the page loaded.
- `classify_failure(error=None, response_status=None, session=None, subtitle_probe=None) -> str` returns one `FailureKind` string using explicit evidence.

- [ ] **Step 1: Write failing tests**

```python
import unittest
from pathlib import Path


class FakeDriver:
    def __init__(self, url="https://www.bilibili.com/", text="", state=True):
        self.current_url = url
        self.text = text
        self.state = state

    def execute_script(self, script):
        if "document.body.innerText" in script:
            return self.text
        if "__INITIAL_STATE__" in script:
            return self.state
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


    def test_no_subtitle_is_not_rate_limit(self):
        kind = classify_failure(response_status=200, subtitle_probe={"payload_received": False})
        self.assertEqual(kind, FailureKind.NO_SUBTITLE)


    def test_http_412_is_rate_limited(self):
        kind = classify_failure(response_status=412)
        self.assertEqual(kind, FailureKind.RATE_LIMITED)


    def test_status_and_payload_evidence_are_distinct(self):
        self.assertEqual(classify_failure(response_status=200, subtitle_probe={"payload_received": False}), FailureKind.NO_SUBTITLE)
        self.assertEqual(classify_failure(response_status=None, subtitle_probe={"payload_received": False}), FailureKind.SUBTITLE_API_FAILED)
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `python -m unittest tests.test_bili_guard -v`

Expected: FAIL because `scripts/bili_guard.py` does not exist.

- [ ] **Step 3: Implement the minimal classifier**

Recognize `412`, `429`, `rate limit`, and `too many requests` as `RATE_LIMITED` only when they are present in an HTTP response or an explicit platform signal. Recognize a login URL, login CTA, or a successfully loaded page with no positive account surface as `LOGIN_REQUIRED`; do not use initial-state presence as proof of login. Driver/page access errors are `PAGE_LOAD_FAILED`, not login failures. With a healthy session, classify status `200` plus no subtitle payload as `NO_SUBTITLE`; classify missing response status plus no payload as `SUBTITLE_API_FAILED`. Do not classify every exception as rate limiting.

- [ ] **Step 4: Add preflight to the shared extractor**

After `driver.get(video_url)` and the existing page wait, call `check_login_state(driver)`. If it is not healthy, record `status.reason` (`LOGIN_REQUIRED` or `PAGE_LOAD_FAILED`) and return without clicking the subtitle button. Install the subtitle response probe before the existing subtitle action, then read it after the page settles; mid-run login loss must use the same failure path.

- [ ] **Step 5: Run the tests**

Run: `python -m unittest tests.test_bili_guard -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add scripts/bili_guard.py scripts/subtitle_extractor.py tests/test_bili_guard.py
git commit -m "feat: detect Bilibili login and failure states"
```

### Task 2: Add one-process pacing and circuit breaker

**Files:**
- Modify: `scripts/bili_guard.py`
- Modify: `scripts/subtitle_extractor.py:73-90,207-382`
- Test: `tests/test_bili_guard.py`

**Interfaces:**
- `RateLimiter.wait()` blocks only until the next permitted job and then updates its last-start timestamp.
- `CircuitBreaker.record(FailureKind.RATE_LIMITED)` increments consecutive rate-limit failures; `NO_SUBTITLE` and successful capture do not open the circuit. `LOGIN_REQUIRED` and `BROWSER_START_FAILED` pause immediately without a rate-limit count.
- `CircuitBreaker.allow()` returns `False` while `OPEN` and before `retry_after`.

- [ ] **Step 1: Write failing tests for pacing and state transitions**

```python
import unittest


class RateControlTest(unittest.TestCase):
    def test_rate_limiter_waits_between_starts(self):
        sleeps = []
        now = [100.0]
        limiter = RateLimiter(min_delay=8, max_delay=8, clock=lambda: now[0], sleeper=sleeps.append)
        limiter.wait()
        now[0] = 101.0
        limiter.wait()
        self.assertEqual(sleeps, [7.0])


    def test_two_rate_limits_open_circuit_but_no_subtitle_does_not(self):
        breaker = CircuitBreaker(state_path=Path("guard-state.json"), threshold=2, cooldown_seconds=900, clock=lambda: 100.0)
        breaker.record(FailureKind.NO_SUBTITLE)
        self.assertEqual(breaker.state, "CLOSED")
        breaker.record(FailureKind.RATE_LIMITED)
        breaker.record(FailureKind.RATE_LIMITED)
        self.assertEqual(breaker.state, "OPEN")
        self.assertFalse(breaker.allow())


    def test_circuit_enters_half_open_after_cooldown(self):
        now = [100.0]
        breaker = CircuitBreaker(state_path=Path("guard-state.json"), threshold=1, cooldown_seconds=900, clock=lambda: now[0])
        breaker.record(FailureKind.RATE_LIMITED)
        now[0] = 1000.0
        self.assertTrue(breaker.allow())
        self.assertEqual(breaker.state, "HALF_OPEN")
```

Add tests for a fresh `CircuitBreaker` instance reading an open state, honoring `retry_after`, and entering `HALF_OPEN` only after cooldown. Add a test that a successful capture resets the consecutive counter, while `NO_SUBTITLE` does not. Use a temporary directory for the state file so the test leaves no repository artifact.

- [ ] **Step 2: Run the tests and verify they fail**

Run: `python -m unittest tests.test_bili_guard -v`

Expected: FAIL on missing limiter/breaker behavior.

- [ ] **Step 3: Implement the limiter and breaker**

Use `time.monotonic()` for elapsed time and the persisted UTC `retry_after` for restart checks. The limiter samples a delay in `[min_delay, max_delay]`; tests pass fixed `rng` or equal bounds. The breaker opens immediately for `LOGIN_REQUIRED`, opens after two consecutive observed `RATE_LIMITED` events, and resets on a successful subtitle capture. A 412/429 is never retried immediately.

- [ ] **Step 4: Integrate before each video**

In `SubtitleExtractor.__init__`, accept `min_delay`, `max_delay`, `rate_limit_threshold`, and `cooldown_seconds`, and create one guard for the extractor/output directory. Before every video, call `breaker.allow()` then `limiter.wait()`. After success call `breaker.reset()`; after classification call `breaker.record(kind)`. Remove any recursive `extract_single()` retry; only the guard may retry browser/page startup once.

- [ ] **Step 5: Expose conservative CLI options**

Add these options to `subtitle_extractor.py` and pass them into `SubtitleExtractor`:

```text
--min-delay 8
--max-delay 15
--rate-limit-threshold 2
--cooldown-seconds 900
```

Reject negative values and reject `max-delay < min-delay` through `argparse` validation.

- [ ] **Step 6: Run tests and CLI smoke checks**

Run:

```bash
python -m unittest tests.test_bili_guard tests.test_cli_smoke -v
python scripts/subtitle_extractor.py --help
```

Expected: all tests pass; help shows the four options without starting a browser.

- [ ] **Step 7: Commit**

```bash
git add scripts/bili_guard.py scripts/subtitle_extractor.py tests/test_bili_guard.py tests/test_cli_smoke.py
git commit -m "feat: add paced subtitle extraction and circuit breaker"
```

### Task 3: Persist failure reasons and stop batch execution safely

**Files:**
- Modify: `scripts/subtitle_extractor.py:159-176,365-382`
- Modify: `scripts/run_subtitle_batch.py:48-125`
- Modify: `scripts/rescue_one_subtitle.py:175-220`
- Test: `tests/test_failure_persistence.py`

**Interfaces:**
- `_update_download_list()` accepts `error_code` and optional `retry_after` while preserving existing records; its read/modify/write is protected by one process lock.
- `run_one()` returns exactly `(status, message, error_code)`; the batch loop stops when the breaker is open or a pause code is returned.
- Rescue mode records the same failure codes rather than creating a second status vocabulary. Search performs the same login preflight before collecting IDs.
- Process exit codes are stable: `0` normal completion, `10` rate-limit pause, `11` login pause, `12` browser-start pause.

- [ ] **Step 1: Write failing persistence tests**

```python
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


class FailurePersistenceTest(unittest.TestCase):
    def test_rate_limit_failure_is_persisted(self):
        with tempfile.TemporaryDirectory() as directory:
            tmp_path = Path(directory)
            extractor = SubtitleExtractor(output_dir=tmp_path, enrich_with_meta=False)
            extractor._update_download_list(
                "BVTEST123",
                "paused",
                error="HTTP 412",
                error_code="RATE_LIMITED",
                retry_after="2099-01-01T00:00:00",
            )
            record = json.loads((tmp_path / "download_list.json").read_text(encoding="utf-8"))["records"]["BVTEST123"]
            self.assertEqual(record["error_code"], "RATE_LIMITED")
            self.assertEqual(record["retry_after"], "2099-01-01T00:00:00")

    def test_batch_does_not_continue_after_open_circuit(self):
        import run_subtitle_batch

        with tempfile.TemporaryDirectory() as directory:
            bvids_path = Path(directory) / "bvids.json"
            bvids_path.write_text(
                json.dumps({"keyword": "test", "videos": [
                    {"bvid": "BV1", "title": "one"},
                    {"bvid": "BV2", "title": "two"},
                    {"bvid": "BV3", "title": "three"},
                ]}),
                encoding="utf-8",
            )
            fake_module = SimpleNamespace(SubtitleExtractor=lambda **kwargs: object())
            with patch.object(run_subtitle_batch, "patch_selenium_edge", return_value=False), \
                    patch.object(run_subtitle_batch, "load_skill_module", return_value=fake_module), \
                    patch.object(run_subtitle_batch, "run_one", side_effect=[
                        ("failed", "HTTP 412", "RATE_LIMITED"),
                        ("failed", "HTTP 412", "RATE_LIMITED"),
                    ]) as run_one, \
                    patch.object(sys, "argv", [
                        "run_subtitle_batch.py", str(bvids_path), directory, "edge",
                    ]):
                run_subtitle_batch.main()

            self.assertEqual(run_one.call_count, 2)
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `python -m unittest tests.test_failure_persistence -v`

Expected: FAIL because the download-list schema and batch result do not yet carry error codes.

- [ ] **Step 3: Extend the existing record shape**

Add only optional fields:

```json
{
  "status": "paused",
  "error_code": "RATE_LIMITED",
  "error": "HTTP 412",
  "retry_after": "2026-09-21T16:15:00"
}
```

Existing `success`, `failed`, and `subtitle_path` fields remain unchanged.

Write the guard control file with an atomic temporary-file replace and write the run policy into the batch summary:

```json
{
  "guard": {
    "rate_limit_threshold": 2,
    "cooldown_seconds": 900,
    "state_file": ".bili_guard_state.json"
  }
}
```

- [ ] **Step 4: Stop the batch on a global circuit-open result**

When the shared extractor reports `RATE_LIMITED`, `LOGIN_REQUIRED`, or `BROWSER_START_FAILED` after its permitted retry, stop the batch loop, write the current result summary, and return exit code 10, 11, or 12 respectively. Do not mark unvisited videos as failed; they remain pending for the next run. Before a rerun, enforce the persisted `retry_after`; successful records are skipped and pending records remain eligible.

- [ ] **Step 5: Apply the same result contract to rescue mode**

Reuse the failure constants and persist `RATE_LIMITED`/`LOGIN_REQUIRED`/`BROWSER_START_FAILED` in the existing rescue download-list update path. Keep ASR-specific failures separate as `UNKNOWN` or a new `ASR_FAILED` only if the code already needs that distinction. Rescue must use the same persisted state file and must not create a second retry loop.

- [ ] **Step 7: Cover search and all entry points**

Run the login preflight in `fetch_search_bvids.py` before collecting IDs, and pass its output/state directory to the later batch. Verify favorites, space, batch, search, and rescue all emit the same `FailureKind` values and pause semantics.

- [ ] **Step 8: Run tests**

Run: `python -m unittest tests.test_failure_persistence tests.test_rescue_contract -v`

Expected: PASS; a paused batch leaves pending BV IDs available for a later run.

- [ ] **Step 9: Commit**

```bash
git add scripts/subtitle_extractor.py scripts/run_subtitle_batch.py scripts/rescue_one_subtitle.py tests/test_failure_persistence.py
git commit -m "feat: persist rate-limit failures and pause batches"
```

### Task 4: Document operations and complete the verification gate

**Files:**
- Modify: `README.md`
- Modify: `SKILL.md`
- Test: `tests/test_cli_smoke.py`

- [ ] **Step 1: Document the operator behavior**

Add one section covering:

```text
登录失效      → 重新登录 Edge，再重跑
连续 412/429  → 等待 retry_after，再重跑
无 AI 字幕    → 跳过并继续
浏览器崩溃    → 单条重试一次，仍失败则暂停/记录
```

Document the default values and the CLI overrides, the `.bili_guard_state.json` location, the stable exit codes (0/10/11/12), and the resume rule that leaves unvisited videos pending. State explicitly that the project does not solve CAPTCHA, rotate IPs, or spoof fingerprints.

- [ ] **Step 2: Add help assertions**

Assert that `--min-delay`, `--max-delay`, `--rate-limit-threshold`, and `--cooldown-seconds` appear in `python scripts/subtitle_extractor.py --help`.

- [ ] **Step 3: Run the complete gate**

Run:

```bash
python -m unittest discover -s tests -v
Get-ChildItem scripts\*.py | ForEach-Object { python -m py_compile $_.FullName }
git diff --check
```

Expected: all tests pass, all scripts compile, and no whitespace errors are reported.

- [ ] **Step 4: Commit documentation and tests**

```bash
git add README.md SKILL.md tests/test_cli_smoke.py
git commit -m "docs: document Bilibili session and rate-limit handling"
```

## Acceptance Gate

The implementation is ready for agent handoff when:

- A logged-out browser fixture with `window.__INITIAL_STATE__` present but no account surface stops before subtitle extraction with `LOGIN_REQUIRED`.
- A logged-in browser fixture with a positive account surface and no login CTA passes preflight.
- A browser subtitle response probe exposes status 412/429 to Python and classifies it as `RATE_LIMITED`.
- A status-200 response with no subtitle payload is `NO_SUBTITLE`; a missing response status with no payload is `SUBTITLE_API_FAILED`.
- A successful video still produces the same subtitle file and metadata frontmatter.
- A no-subtitle video does not open the circuit.
- Two consecutive observed rate-limit events stop the batch, persist `RATE_LIMITED` plus `retry_after`, and return exit code 10 without an immediate retry.
- A new guard instance honors the persisted `retry_after`; a backward wall-clock jump fails closed.
- Rerunning after cooldown skips successful records and retries pending records only; unvisited videos are not marked failed.
- Browser startup failure retries exactly once, cannot recurse indefinitely, and returns exit code 12 when it remains broken.
- Login pause returns exit code 11 and is handled consistently by favorites, space, batch, search, and rescue.
- `run_one()` returns exactly `(status, message, error_code)` and download-list updates are protected against same-process read/modify/write races.
- `python -m unittest discover -s tests -v` passes.
- All scripts compile and `git diff --check` passes.
- Direct `extract_meta.py` 412 responses return non-zero and do not get treated as successful metadata.

## Deferred Work

- Proxy/IP rotation and multi-account scheduling.
- CAPTCHA handling or browser security-barrier bypass.
- Distributed rate limiting across multiple machines.
- A persistent database for circuit state; the small atomic `.bili_guard_state.json` file is sufficient for the current single-process workflow.
