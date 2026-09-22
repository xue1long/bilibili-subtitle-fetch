# Xiaohongshu Media Download Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a single-note Xiaohongshu CLI that downloads video or all note images and writes sanitized metadata.

**Architecture:** Keep Xiaohongshu in its own media adapter and CLI. Reuse the existing frontmatter writer only for common fields, use Playwright for page state/resource discovery, and use stdlib streaming downloads for CDN assets.

**Tech Stack:** Python stdlib, Playwright, existing `prepend_meta.py`, pytest.

**Spec:** `docs/superpowers/specs/2026-09-22-xiaohongshu-media-design.md`

## Global Constraints

- Single note URL only.
- Video assets are saved as `video.mp4`; image assets are saved as `images/NN.webp`.
- Signed CDN URLs and `xsec_token` stay in memory and never enter output metadata.
- Any asset failure removes the incomplete final output.
- Existing Bilibili and Douyin tests remain green.

## Task 1: Add pure note and asset parsing

**Files:**
- Create: `scripts/xiaohongshu_media.py`
- Test: `tests/test_xiaohongshu_media.py`

**Interfaces:**
- `parse_note_id(url: str) -> str`
- `normalize_metadata(state: dict, source_url: str) -> dict`
- `classify_assets(video_urls: list[str], image_urls: list[str]) -> tuple[str, list[str]]`

- [ ] Write failing tests for `/explore/` and `/board/` IDs, canonical URL sanitization, metadata normalization, video priority, and image de-duplication.
- [ ] Run `python -m pytest tests/test_xiaohongshu_media.py -q` and observe the missing-module failure.
- [ ] Implement the smallest stdlib parser and normalizer.
- [ ] Run the focused tests and confirm they pass.

## Task 2: Add page discovery and streaming asset download

**Files:**
- Modify: `scripts/xiaohongshu_media.py`
- Test: `tests/test_xiaohongshu_media.py`

**Interfaces:**
- `discover_note(url: str, profile_dir: Path) -> tuple[dict, str, list[str]]`
- `download_assets(note: dict, video_url: str | None, image_urls: list[str], output_dir: Path) -> dict`

- [ ] Add a fake-page test proving only note-body images above the large-image threshold are kept, while avatars and UI assets are excluded.
- [ ] Add a local HTTP test or patched opener proving Referer is sent and a failed asset leaves no final output.
- [ ] Implement anonymous Playwright discovery, then persistent `.chrome-xiaohongshu` fallback.
- [ ] Implement Range probing and streamed downloads with temporary files followed by atomic rename.
- [ ] Run `python -m pytest tests/test_xiaohongshu_media.py -q`.

## Task 3: Add CLI and metadata output

**Files:**
- Create: `scripts/xiaohongshu_cli.py`
- Modify: `scripts/prepend_meta.py`
- Test: `tests/test_xiaohongshu_cli.py`, `tests/test_metadata_flow.py`

**Interfaces:**
- `run(url: str, output_dir: Path) -> Path`
- CLI: `python scripts/xiaohongshu_cli.py --url URL [--output DIR]`

- [ ] Add failing tests for video output, image output, sanitized metadata, and cleanup after download failure.
- [ ] Extend frontmatter with `note_id`, `note_type`, `asset_count`, and `assets` only when present.
- [ ] Implement the CLI with output at `10_raw/03_小红书/<note_id>/` and no partial final Markdown.
- [ ] Run the focused CLI and metadata tests.

## Task 4: Document and verify

**Files:**
- Modify: `.gitignore`, `README.md`, `SKILL.md`, `AGENT.md`, `SETUP.md`

- [ ] Ignore `.chrome-xiaohongshu/` and keep `10_raw/` local-only.
- [ ] Document video and image commands, output layout, profile override, and no-token rule.
- [ ] Run the full suite with `python -m pytest -q`.
- [ ] Run live smoke tests against one video note and one image note; verify asset counts and frontmatter.
- [ ] Run `git diff --check` and inspect `git status --short`.
