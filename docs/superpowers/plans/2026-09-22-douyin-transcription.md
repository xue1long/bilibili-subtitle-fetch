# Douyin Video Transcription Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a single-video Douyin download → audio extraction → faster-whisper → metadata/frontmatter pipeline without changing the existing Bilibili task model.

**Architecture:** Add a small Douyin-specific CLI and browser/download module. Start with an anonymous temporary Playwright context, then fall back to `yt-dlp` and an optional persistent profile. Reuse the existing SRT formatter and extend the generic frontmatter writer with platform-neutral identity fields. Keep Douyin output and state separate from Bilibili.

**Tech Stack:** Python stdlib, Playwright, yt-dlp, ffmpeg, faster-whisper, existing `prepend_meta.py`.

**Spec:** `docs/superpowers/specs/2026-09-22-douyin-transcription-design.md`

## Global Constraints

- Default output is `10_raw/02_抖音视频转录/`.
- Anonymous extraction is first; `.chrome-douyin` is only the authenticated fallback and `DOUYIN_SFETCH_CHROME_PROFILE` overrides it.
- Do not persist cookies, Authorization headers, response bodies, or raw media URLs.
- Metadata failure is partial success; download or ASR failure leaves no successful `.md`.
- Existing Bilibili tests must remain green.

### Task 1: Make frontmatter platform-neutral

**Files:**
- Modify: `scripts/prepend_meta.py`
- Test: `tests/test_metadata_flow.py`

**Interfaces:**
- Preserve existing Bilibili keys and behavior.
- Accept optional `platform`, `video_id`, `url`, and `transcript_model` keys.

- [ ] **Step 1: Write the failing test**

Add a test that calls `prepend_meta()` with Douyin metadata and asserts the output contains `platform`, `video_id`, `url`, `description`, and the original SRT body.

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_metadata_flow.py -q`
Expected: the new test fails because `prepend_meta.py` currently only emits Bilibili `bv`/`bvid` fields.

- [ ] **Step 3: Implement the smallest writer change**

Emit generic identity fields when present, keep `bv` mapping for Bilibili, and emit `transcript_model` when provided. Do not change the existing Bilibili field order or description block behavior.

- [ ] **Step 4: Run the focused test**

Run: `pytest tests/test_metadata_flow.py -q`
Expected: PASS.

### Task 2: Add Douyin page resolution and media download

**Files:**
- Create: `scripts/douyin_media.py`
- Test: `tests/test_douyin_media.py`

**Interfaces:**
- `parse_video_id(value: str) -> str`
- `extract_page_metadata(state: dict, source_url: str) -> dict`
- `download_video(url: str, output_path: Path, profile_dir: Path) -> dict`

- [ ] **Step 1: Write failing pure parsing tests**

Cover `modal_id` extraction from the supplied URL, preservation of title/uploader/description, and missing-description tolerance.

- [ ] **Step 2: Run the focused tests**

Run: `pytest tests/test_douyin_media.py -q`
Expected: FAIL because the module and functions do not exist.

- [ ] **Step 3: Implement pure parsing first**

Use `urllib.parse` for URL IDs. Normalize common page-state keys into `platform`, `video_id`, `title`, `uploader`, `description`, `video_published_at`, and `url`; omit unavailable optional values. Reject a media candidate unless its surrounding page state is associated with the requested `modal_id`.

- [ ] **Step 4: Add the download adapter**

Launch a temporary anonymous Playwright context first, open the source URL, match the requested `modal_id` to its `playAddr`, issue a small Range request, and stream the video. If anonymous extraction fails, try `yt-dlp`; then use a persistent Playwright context from `.chrome-douyin` or `DOUYIN_SFETCH_CHROME_PROFILE`. Return metadata only, never raw URLs.

- [ ] **Step 5: Run focused tests**

Run: `pytest tests/test_douyin_media.py -q`
Expected: pure parser tests pass; downloader contract tests use a fake runner/browser and do not require live credentials.

### Task 3: Add audio extraction and transcription orchestration

**Files:**
- Create: `scripts/douyin_cli.py`
- Modify: `scripts/subtitle_rescue.py` only if a generic SRT helper must be extracted
- Test: `tests/test_douyin_cli.py`

**Interfaces:**
- `run(url: str, output_dir: Path, model_size: str = "small") -> Path`
- CLI: `python scripts/douyin_cli.py --url URL [--output DIR] [--model MODEL] [--keep-video]`

- [ ] **Step 1: Write failing orchestration tests**

Test that successful fake download + fake transcription creates a `.md` file with metadata before SRT, and that a failed download/ASR does not create a final `.md`.

- [ ] **Step 2: Run focused tests**

Run: `pytest tests/test_douyin_cli.py -q`
Expected: FAIL because the CLI does not exist.

- [ ] **Step 3: Implement minimal orchestration**

Create a temporary directory below `tmp/douyin`, call the media adapter, run ffmpeg to extract audio, reuse the existing faster-whisper SRT formatter/transcriber, write the SRT file atomically, then call `prepend_meta()` with normalized Douyin metadata plus `transcript_model`.

- [ ] **Step 4: Add cleanup and error codes**

Delete temporary video/audio on success and failure unless `--keep-video` is passed. Map the five spec error classes to stderr and a non-zero exit code.

- [ ] **Step 5: Run focused tests**

Run: `pytest tests/test_douyin_cli.py -q`
Expected: PASS.

### Task 4: Add setup/documentation and run the live smoke test

**Files:**
- Modify: `README.md`
- Modify: `SKILL.md`
- Modify: `AGENT.md`
- Modify: `SETUP.md`

- [ ] **Step 1: Document the command and dependencies**

Document `faster-whisper`, `ffmpeg`, anonymous extraction, the optional `.chrome-douyin` profile, metadata fields, output path, and the provided URL as a one-video example.

- [ ] **Step 2: Run the full test suite**

Run: `pytest -q`
Expected: all existing and new tests pass.

- [ ] **Step 3: Run the live smoke test**

Run the provided URL with `--model small`; verify anonymous extraction first and only exercise the logged-in fallback if needed. Verify the output has YAML frontmatter and at least one SRT timestamp. Redact cookies and raw media URLs from all captured output.

- [ ] **Step 4: Check the worktree**

Run: `git diff --check; git status --short`
Expected: only intended source, test, and documentation files are changed.
