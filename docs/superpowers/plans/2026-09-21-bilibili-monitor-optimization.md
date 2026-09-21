# Bilibili Batch Monitoring Optimization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Improve the three issues observed in the four-video live run: unreliable search titles, weak subtitle failure evidence, and repeated browser startup overhead.

**Priority:** Tasks 1-4 are mandatory acceptance scope. The optional section is explicitly outside the release gate and must not delay delivery.

**Architecture:** Keep the current single-process batch flow and extend its existing guard/probe interfaces. Search results gain a title-quality filter with a detail-page fallback; subtitle extraction exposes a compact probe snapshot and per-video timing; batch mode optionally reuses one browser driver for the whole run. All new result fields remain optional for backward compatibility.

**Tech Stack:** Python 3.7+, Selenium, Playwright, `unittest`; standard library only for the new logic.

**Spec:** Existing risk-control plan in [`2026-09-21-bilibili-risk-control-hardening.md`](2026-09-21-bilibili-risk-control-hardening.md).

## Baseline from the four-video run

- Input: `hyperframes`, 4 BVs.
- Result: 2 subtitle files, 2 `SUBTITLE_API_FAILED`, exit code `2`.
- No `LOGIN_REQUIRED`, `RATE_LIMITED`, or circuit-open event.
- Runtime: 146.6 seconds total, about 36.7 seconds per video.
- Search metadata: 3 of 4 titles were play-count/duration text instead of video titles.
- Failed records showed `url=False, data=False`, so the current result cannot distinguish “no AI subtitle” from “subtitle request was never observed”.

## Global Constraints

- Preserve the existing output directory, `download_list.json`, and successful-record resume behavior.
- Keep execution single-process and serial; no proxy rotation, fingerprint spoofing, CAPTCHA handling, or parallel browser jobs.
- Never persist cookies, authorization headers, response bodies, page text, or raw response URLs.
- Keep Python 3.7 compatibility and use `unittest`.
- New JSON fields are optional; old result files remain readable.
- Browser reuse is limited to one batch process and must have an explicit close path.
- Title enrichment is best-effort and must never block subtitle extraction.
- Only observed 412/429 responses may trigger the global risk pause. Ordinary subtitle misses must remain per-video failures.
- A detail-title fallback may run only with the shared limiter; without a limiter it records `unresolved` and makes no extra request.
- The live acceptance run uses a clean output directory and is evidence collection, not the sole correctness test.

---

### Task 1: Make subtitle failures observable

**Files:**
- Modify: `scripts/bili_guard.py`
- Modify: `scripts/subtitle_extractor.py`
- Modify: `scripts/run_subtitle_batch.py`
- Test: `tests/test_bili_guard.py`
- Test: `tests/test_failure_persistence.py`

**Interfaces:**
- Add `FailureKind.SUBTITLE_NOT_OBSERVED`.
- `read_subtitle_probe(driver)` returns only:

```python
{
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
}
```

The four stage fields distinguish an unobserved request from a page or click failure without persisting page text, cookies, headers, bodies, or URLs.

- Add `SubtitleExtractor.last_probe` and `SubtitleExtractor.last_duration_sec` for the batch runner.

- [ ] **Step 1: Write failing classifier tests**

```python
def test_unobserved_subtitle_request_is_distinct(self):
    kind = classify_failure(
        subtitle_probe={
            "request_observed": False,
            "response_status": None,
            "payload_received": False,
        }
    )
    self.assertEqual(kind, FailureKind.SUBTITLE_NOT_OBSERVED)
```

- [ ] **Step 2: Implement the probe state**

Set the stage fields at the corresponding lifecycle points. Set `request_observed=True` and increment `request_count` when a matching Fetch URL or XHR URL is seen. Preserve the first observed 412/429 status even if a later request returns 200. Read the probe only after the existing subtitle wait timeout has elapsed, so asynchronous requests are not classified prematurely. Keep only the compact fields in the probe.

- [ ] **Step 3: Implement the classification matrix**

Use this exact order:

| Evidence | Failure kind |
|---|---|
| HTTP 412 or 429 | `RATE_LIMITED` |
| no matching request observed | `SUBTITLE_NOT_OBSERVED` |
| request observed, status 200, no valid body | `NO_SUBTITLE` |
| request observed, status missing/0, no body | `SUBTITLE_API_FAILED` |
| valid subtitle body | success path |

Only `RATE_LIMITED` opens the global circuit. `SUBTITLE_NOT_OBSERVED`, `NO_SUBTITLE`, and `SUBTITLE_API_FAILED` remain per-video failures.

- [ ] **Step 4: Persist compact diagnostics**

Extend the current download record and batch result item with optional fields:

```json
{
  "error_code": "SUBTITLE_NOT_OBSERVED",
  "probe": {
    "request_observed": false,
    "response_status": null,
    "payload_received": false,
    "subtitle_count": 0,
    "language_count": 0,
    "hook_installed": true,
    "page_ready": true,
    "button_found": true,
    "click_dispatched": true,
    "request_count": 0
  },
  "duration_sec": 35.2
}
```

Every new result, including circuit-open and browser-start failures, gets a stable `duration_sec` value and a probe object with the same keys. Missing values in old records default to `None`/`False`; add `schema_version=2` only to new records.

- [ ] **Step 5: Test the complete evidence matrix**

Cover: hook not installed, page not ready, click not dispatched, no request, 200 without subtitle, missing status, 412, 429, and valid subtitle body. Verify that only 412/429 opens the circuit.

- [ ] **Step 6: Test and commit**

Run:

```bash
python -m unittest tests.test_bili_guard tests.test_failure_persistence -v
```

Completion criterion: failed live-style fixtures show whether a request was observed, without persisting sensitive browser data.

---

### Task 2: Correct search-result titles

**Files:**
- Modify: `scripts/fetch_search_bvids.py`
- Modify: `scripts/run_subtitle_batch.py`
- Test: `tests/test_search_metadata.py`

**Interfaces:**
- Add `clean_search_title(raw_title) -> str`.
- Add `is_metric_title(title) -> bool`.
- Extend `fetch_page(page, keyword, page_num, limiter)`; the caller owns one shared limiter for the search batch.
- Add `resolve_detail_title(page, bvid, limiter) -> str`; a missing limiter returns `unresolved` without opening a detail page.
- Each search video record may contain `title_source` with values `card`, `detail`, or `unresolved`.

- [ ] **Step 1: Write failing title tests**

```python
def test_metric_text_is_not_a_video_title(self):
    self.assertTrue(is_metric_title("7299\n2\n41:06"))
    self.assertEqual(clean_search_title("7299\n2\n41:06"), "")


def test_real_card_title_is_preserved(self):
    self.assertEqual(clean_search_title("量化交易入门：从数据到策略"), "量化交易入门：从数据到策略")
```

- [ ] **Step 2: Implement card-title extraction**

Read card `title`, `aria-label`, and `data-title` attributes before falling back to visible text. Normalize whitespace and strip HTML tags. Reject values that contain only play count, comment count, duration, or ranking text.

- [ ] **Step 3: Add a detail-page fallback**

For a rejected card title, call the fallback only when the shared limiter is available and wait before the request. Use a temporary page in the same browser context, close it in `finally`, and leave the search page unchanged. Read the page title or `window.__INITIAL_STATE__.videoData.title`; reject generic login, risk-control, and platform titles. If it still fails, write an empty title with `title_source="unresolved"`; never write metric text as the title.

- [ ] **Step 4: Preserve batch behavior**

The batch runner must display `BV` plus an empty/unresolved title safely and continue downloading. A title failure must not become a subtitle failure.

- [ ] **Step 5: Test and commit**

Run:

```bash
python -m unittest tests.test_search_metadata tests.test_cli_smoke -v
```

Completion criterion: metric-only strings never appear as a saved video title; unresolved titles are explicit and non-fatal.

---

### Task 3: Reuse one browser per batch

**Files:**
- Modify: `scripts/subtitle_extractor.py`
- Modify: `scripts/run_subtitle_batch.py`
- Test: `tests/test_browser_lifecycle.py`
- Modify: `README.md`
- Modify: `SKILL.md`

**Interfaces:**
- Add `SubtitleExtractor(reuse_browser=False)`.
- Add `SubtitleExtractor.close()`; it is idempotent.
- Batch mode constructs `SubtitleExtractor(reuse_browser=True)` and calls `close()` in `finally`.
- Standalone/favorites/space modes keep `reuse_browser=False`; they only gain the same idempotent close path.

- [ ] **Step 1: Write lifecycle tests**

```python
class FakeDriver:
    def __init__(self):
        self.get_count = 0
        self.quit_count = 0

    def get(self, url):
        self.get_count += 1

    def quit(self):
        self.quit_count += 1


def test_batch_reuses_one_driver(self):
    extractor = SubtitleExtractor(output_dir=Path(tempfile.mkdtemp()), reuse_browser=True)
    driver = FakeDriver()
    with patch.object(extractor, "_start_driver", return_value=driver) as start:
        extractor._get_driver()
        extractor._get_driver()
        extractor.close()
    self.assertEqual(start.call_count, 1)
    self.assertEqual(driver.quit_count, 1)


def test_close_is_idempotent(self):
    extractor = SubtitleExtractor(output_dir=Path(tempfile.mkdtemp()), reuse_browser=True)
    extractor.close()
    extractor.close()
```

The test module imports `tempfile`, `Path`, `patch`, and `SubtitleExtractor` before these tests.

- [ ] **Step 2: Extract driver lifecycle methods**

Move browser construction to `_start_driver()` and navigation to `_get_driver().get(video_url)`. Store the driver on the extractor only when `reuse_browser=True`; preserve current per-video lifecycle when it is false. Before each video, navigate through the same page, wait for readiness, and install the network hook once for that page load. A navigation must not reuse probe state from the previous video.

- [ ] **Step 3: Handle one browser retry safely**

On `BROWSER_START_FAILED` or `PAGE_LOAD_FAILED`, close and clear the cached driver, then recreate it once for the same video. If recreation fails, record the failure and continue with the normal batch stop policy. Login and rate-limit failures never recreate or immediately retry the browser. Track `browser_start_count` and `browser_restart_count` in the in-memory batch summary; do not persist driver details.

- [ ] **Step 4: Close every entry point**

Wrap batch, favorites, space, and CLI execution in `try/finally` with `extractor.close()`. A successful subtitle must be fully written before the browser is closed.

- [ ] **Step 5: Record per-video timing**

Set `last_duration_sec = round(time.monotonic() - started, 1)` in `extract_single`, including circuit-open, browser-start, and page-load failures. The batch result reads `last_probe` and `last_duration_sec` immediately after `extract_single`, so the record schema does not depend on a hidden tuple shape. Print the average only after the run completes.

- [ ] **Step 6: Test and commit**

Run:

```bash
python -m unittest tests.test_browser_lifecycle tests.test_failure_persistence -v
```

Completion criterion for the healthy path: a four-video batch starts one browser driver, closes it once, records four durations, resets probe state per video, and still pauses safely on login/rate-limit conditions. A driver restart is allowed only in the explicitly tested failure path.

---

### Task 4: Full verification and operations update

**Files:**
- Modify: `README.md`
- Modify: `SKILL.md`
- Test: `tests/test_cli_smoke.py`

- [ ] **Step 1: Document the new monitoring fields**

Document `title_source`, `probe`, `duration_sec`, `SUBTITLE_NOT_OBSERVED`, and the distinction between ordinary subtitle misses and global pauses.

- [ ] **Step 2: Run deterministic acceptance tests**

Run the complete unit suite before the live test. The suite must assert the evidence matrix, title-source assignment, temporary detail-page cleanup, old-record defaults, browser restart limit, and `finally` closure.

- [ ] **Step 3: Run the four-video acceptance test**

```bash
python scripts/fetch_search_bvids.py hyperframes 4 10_raw/monitor_4_optimization
python scripts/run_subtitle_batch.py \
  10_raw/monitor_4_optimization/.batch/hyperframes_4_bvids.json \
  10_raw/monitor_4_optimization edge
```

Run against a new output directory. Verify the result JSON has four records, every record has `duration_sec` and a complete probe, metric-only titles are absent, `title_source` is accurate, browser start/close counts match the healthy-path contract, and `.bili_guard_state.json` remains closed unless a real 412/429 occurs. A live subtitle failure is acceptable only when its probe evidence is internally consistent; live success rate is not the release gate because Bilibili state is external.

- [ ] **Step 4: Run the complete gate**

```bash
python -m unittest discover -s tests -v
Get-ChildItem scripts\*.py | ForEach-Object { python -m py_compile $_.FullName }
git diff --check
```

Completion criterion: all deterministic tests pass, all scripts compile, and the live four-video report provides enough evidence to distinguish title quality, subtitle-request observation, browser cost, and global risk pauses.

---

## Optional follow-ups

These are useful after the mandatory gate passes, but are not required for delivery:

- Add batch-level P95 duration, per-kind counters, and a fallback-request count.
- Add a small title cache to avoid enriching the same BVID repeatedly.
- Add compatibility tests for malformed or partially written historical JSON.
- Run a second browser-engine smoke test if Chrome support is a delivery requirement.
- Normalize documentation of batch input/output paths.

Do not add proxy rotation, fingerprint spoofing, CAPTCHA handling, distributed locking, ML title scoring, or a full alerting platform in this round.

## Rollback Boundary

- `reuse_browser=False` restores the current per-video browser lifecycle.
- Removing optional `probe`, `duration_sec`, and `title_source` fields leaves existing readers compatible.
- If detail-page title fallback increases rate-limit events, disable only that fallback and retain metric-title rejection.
