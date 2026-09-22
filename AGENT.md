# Agent Guide

这是一个用 Playwright 抓取 B 站 AI 字幕、并可用本地 ASR 救援的 Python 工具。

## 入口与结构

- `scripts/cli.py`：推荐入口，支持单视频、收藏夹 URL、UP 主空间 URL，以及任务重试。
- `scripts/subtitle_extractor.py`：兼容旧入口；默认 Playwright，可用 `--backend selenium` 回退。
- `scripts/fetch_search_bvids.py` / `scripts/run_subtitle_batch.py`：搜索并批量下载。
- `scripts/rescue_one_subtitle.py`：单条视频的 DASH 音频 + faster-whisper 救援。
- `scripts/douyin_cli.py`：抖音单视频匿名优先下载、音频提取和 faster-whisper 转录。
- `scripts/sources/`：视频来源适配器。
- `scripts/pipeline/`：任务模型和去重规划。
- `scripts/backends/`：Playwright 字幕与 ASR 后端。
- `scripts/storage/`：manifest、SQLite 状态库和旧 JSON 迁移。
- `tests/`：单元测试与入口/浏览器生命周期测试。
- `10_raw/01_B站视频转录/`：默认字幕输出目录。
- `10_raw/02_抖音视频转录/`：抖音字幕输出目录。

## 技能索引

| Skill | 用途 |
|---|---|
| [SKILL.md](SKILL.md) | B 站字幕抓取、批量下载和 ASR 救援的操作约定 |

## 常用命令

安装依赖并下载 Playwright 浏览器：

```powershell
pip install -r requirements.txt
playwright install chromium
```

推荐入口：

```powershell
python scripts\cli.py --video BVxxxxxxxxxx
python scripts\cli.py --favorite-url "https://space.bilibili.com/.../favlist?fid=..."
python scripts\cli.py --space-url "https://space.bilibili.com/.../upload/video"
python scripts\douyin_cli.py --url "https://www.douyin.com/user/self?modal_id=...&showTab=favorite_collection"
```

启用 ASR 兜底或重试任务：

```powershell
python scripts\cli.py --video BVxxxxxxxxxx --asr-fallback --asr-model small
python scripts\cli.py --favorite-url "..." --retry-failed
```

运行测试：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

## 运行约定

- 默认后端是 Playwright；Selenium 仅作为显式兼容回退。
- 默认 Chrome profile 是项目根目录的 `.chrome-bilibili`；设置 `BILIBILI_SFETCH_CHROME_PROFILE` 可覆盖。
- 字幕输出为 SRT 内容，文件扩展名为 `.md`；默认路径可用 `--output` 覆盖。
- `data/videos_manifest.json` 保存来源；`data/subtitle_tasks.db` 是统一入口的任务状态主库。
- `download_list.json` 是旧入口、批处理和救援流程共用的兼容记录；CLI 会从旧 JSON 迁移，但任务规划以 SQLite 为准。
- `scripts/compile_db.json` 仅在文件存在且含对应 record 时写入字幕路径，不是任务状态库。
- ASR 需要 `faster-whisper` 和系统 `ffmpeg`；空间模式需要 `yt-dlp`。
- 抖音流程先匿名提取；失败才使用 `yt-dlp` 和可选的 `.chrome-douyin` profile，环境变量 `DOUYIN_SFETCH_CHROME_PROFILE` 可覆盖。
- 不提交 `.chrome-bilibili/`、`.venv/`、字幕、音频、模型缓存或账号状态。
- 不记录或提交 Cookie、Authorization header、响应 body、页面文本和原始响应 URL；不绕过 CAPTCHA、付费限制或风控。

## 关键行为

1. 来源适配器发现并去重 BV 号，写入 manifest。
2. 任务规划器根据 SQLite 状态跳过成功项，并保留失败、暂停项供重试。
3. Playwright 打开持久化 profile，监听 AI 字幕响应并写出 SRT 内容。
4. 原生字幕确认不存在且显式启用 `--asr-fallback` 时，才下载音频并本地转录。
5. 登录失败、浏览器启动失败和真实 412/429 限流会暂停任务；批量流程不把未访问视频标为失败。
6. 抖音媒体候选必须与目标 `modal_id` 关联；不使用页面中的第一个候选。
