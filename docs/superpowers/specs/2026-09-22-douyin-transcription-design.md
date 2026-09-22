# 抖音收藏夹视频转录设计

## 目标

给定一个抖音视频 URL，下载视频，提取音频，用 faster-whisper 转录，并生成带抖音视频元数据和 SRT 字幕的 `.md` 文件。

## 范围

- 第一版只处理一个视频 URL；URL 中的 `modal_id` 作为视频标识。
- 首先使用无 Cookie 的临时 Playwright 上下文，支持抖音页面已自动加载但提示登录的场景。
- 元数据优先从匿名页面状态或页面 JSON 中提取：标题、作者、简介、发布时间、视频 ID、原始 URL。
- 媒体 URL 必须与 URL 中的 `modal_id` 精确关联；先做 Range 探测再流式下载。匿名解析失败时，才回退到 `yt-dlp` 和可选的 `.chrome-douyin` 登录态，支持 `DOUYIN_SFETCH_CHROME_PROFILE` 覆盖。
- 音频通过 ffmpeg 提取，使用 faster-whisper 生成 SRT，临时视频和音频在成功后删除。
- 输出到 `10_raw/02_抖音视频转录/DY<video_id>.md`。

## 不做

- 不改造现有 B 站 `Video.bvid` 和 SQLite schema。
- 不批量扫描收藏夹，不处理 CAPTCHA、付费限制、DRM 或绕过风控。
- 不持久化 Cookie、Authorization header、响应 body 或原始媒体 URL。
- 不使用页面中与目标 `modal_id` 无法关联的第一个媒体候选。

## 输出格式

```yaml
---
platform: douyin
video_id: "7687559858779351972"
title: ...
uploader: ...
description: |
  ...
video_published_at: ...
url: https://www.douyin.com/video/...
transcript_model: small
---
1
00:00:00,000 --> 00:00:02,000
...
```

## 失败语义

- `LOGIN_REQUIRED`：匿名页面和可选的登录 profile 都无法访问目标视频。
- `VIDEO_NOT_FOUND`：无法从 URL 或页面解析视频 ID。
- `VIDEO_DOWNLOAD_FAILED`：视频或媒体流下载失败。
- `AUDIO_EXTRACT_FAILED`：ffmpeg 提取音频失败。
- `ASR_FAILED`：faster-whisper 转录失败。
- `METADATA_PARTIAL`：字幕成功但部分元数据缺失；不阻断产物生成。

## 验收标准

1. 对用户提供的 URL 能生成一个 `.md` 文件。
2. 文件包含 `platform`, `video_id`, `title`, `url`，简介存在时包含 `description`。
3. frontmatter 后存在至少一段合法 SRT 时间轴。
4. 下载失败、转录失败不会留下伪成功记录或半成品 `.md`。
5. 现有 B 站测试全部保持通过。
