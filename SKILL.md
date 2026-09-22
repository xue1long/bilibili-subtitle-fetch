---
name: bilibili-subtitle-fetch
description: B站字幕抓取技能。用户要求下载、提取、导出 AI 字幕，搜索批量下载，或用音频 ASR 救援时使用。
context: fork
agent: general-purpose
---

# Bilibili AI Subtitle Fetch

用 Playwright 持久化浏览器抓取 B 站 AI 字幕，保存为 SRT 内容的 `.md` 文件；Selenium 只作显式兼容回退，原生字幕不存在时可显式启用 faster-whisper ASR。

## 入口

| 需求 | 命令 |
|---|---|
| 单视频 | `python scripts/cli.py --video BVxxxxxxxxxx` |
| 收藏夹 URL | `python scripts/cli.py --favorite-url "https://space.bilibili.com/.../favlist?fid=..."` |
| UP 主空间 | `python scripts/cli.py --space-url "https://space.bilibili.com/.../upload/video"` |
| ASR 兜底 | 在以上命令追加 `--asr-fallback --asr-model small` |
| 重试失败 | 追加 `--retry-failed`；暂停任务另加 `--retry-paused` |
| 旧单视频入口 | `python scripts/subtitle_extractor.py BVxxxxxxxxxx` |
| 单条 ASR 救援 | `python scripts/rescue_one_subtitle.py BVxxxxxxxxxx --model small` |
| 抖音单视频 | `python scripts/douyin_cli.py --url "https://www.douyin.com/user/self?modal_id=...&showTab=favorite_collection"` |

推荐使用 `cli.py`：它发现来源、写入 manifest，并用 SQLite 规划可恢复任务。旧入口仍保留 `--space`、`--favorites`、`--output`、`--no-meta`、`--backend` 等选项；`--favorites` 读取项目根目录的 `videos_fav.json`。

## 搜索批量

先抓搜索结果，再下载字幕：

```powershell
python scripts/fetch_search_bvids.py "关键词" 20
python scripts/run_subtitle_batch.py
```

默认输出目录是 `10_raw/01_B站视频转录/`，中间文件在 `.batch/`，结果写入 `<keyword>_results.json`。默认后端是 Playwright；批处理需要旧 Selenium 流程时传 `--backend selenium`，此时位置参数 `edge` 才切换到 Edge。批处理默认每条间隔 8–15 秒，连续两次实际 412/429 后暂停 900 秒。

## 前置条件

- Python 3.7+，安装 `requirements.txt` 并运行 `playwright install chromium`。
- 已登录 B 站的项目 profile：默认是项目根目录 `.chrome-bilibili`，可用 `BILIBILI_SFETCH_CHROME_PROFILE` 覆盖。
- 空间来源需要 `yt-dlp`；ASR 需要 `faster-whisper` 和系统 `ffmpeg`。
- 同一个持久化 profile 不能被多个浏览器进程同时占用；登录脚本为 `python scripts/open_playwright_login.py`。

## 状态与输出

- 字幕：`10_raw/01_B站视频转录/{BV}.md`，内容是 SRT；`--output` 可覆盖目录。
- 来源：`data/videos_manifest.json`。
- 统一任务状态主库：`data/subtitle_tasks.db`。
- 旧入口、批处理和救援共用：`{output_dir}/download_list.json`；CLI 会从 `data/subtitle_jobs.json` 和该文件迁移旧记录。
- 限流状态：`{output_dir}/.bili_guard_state.json`。
- `scripts/compile_db.json` 只在存在对应 Wiki record 时补写 `subtitle_srt_path`，不承担任务状态。

成功字幕会尽力写入 YAML frontmatter。Playwright 优先从当前已登录页面的 `window.__INITIAL_STATE__.videoData` 读取元数据，避免额外无 Cookie 请求；读取失败不影响字幕主流程。使用 `--no-meta` 关闭。

## 抖音单视频

`douyin_cli.py` 先用无 Cookie 临时 Playwright 页面提取与 `modal_id` 精确关联的媒体 URL，并通过 Range 探测后流式下载；页面提示登录不等于不能下载。匿名解析失败后才回退 `yt-dlp`，最后才使用 `.chrome-douyin` 登录 profile。输出为 `10_raw/02_抖音视频转录/DY<视频ID>.md`，包含 `platform`、`video_id`、`title`、`uploader`、`description`（有则写入）、`url` 和 `transcript_model`。

```powershell
python scripts/douyin_cli.py --url "https://www.douyin.com/user/self?modal_id=7687559858779351972&showTab=favorite_collection"
```

## 处理规则

1. 来源按 BV 号去重；统一入口跳过 SQLite 中成功的任务。
2. 浏览器启动或页面加载失败最多重试一次。
3. 登录失败、浏览器启动失败和熔断中的限流任务标记为暂停；未访问的视频不标记为失败。
4. `SUBTITLE_NOT_OBSERVED`、`NO_SUBTITLE`、`SUBTITLE_API_FAILED`、`LOGIN_REQUIRED`、`RATE_LIMITED` 等错误码必须保留原语义。
5. 只有真实 412/429 计入熔断；普通无字幕只影响当前视频。
6. 监控记录只保存结构化 probe 和耗时，不保存 Cookie、Authorization header、响应 body、页面文本或原始响应 URL。
7. 不处理 CAPTCHA，不轮换代理/IP，不伪装浏览器指纹。

## 代码位置

- `scripts/sources/`：single video、favorite、space 来源适配器。
- `scripts/pipeline/`：`SubtitleTask`、状态枚举和规划器。
- `scripts/backends/`：Playwright 字幕和 ASR 后端。
- `scripts/storage/`：manifest、SQLite 和旧 JSON 存储。
- `scripts/extract_meta.py` / `scripts/prepend_meta.py`：元数据读取与 frontmatter 写入。
- `scripts/douyin_media.py` / `scripts/douyin_cli.py`：抖音匿名优先下载、音频提取和 ASR 转录。

修改抓取、状态或风控逻辑后运行：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```
