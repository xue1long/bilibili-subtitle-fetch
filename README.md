# bilibili-subtitle-fetch

> B 站 AI 字幕下载工具 — Playwright + SQLite 可恢复流水线
>
> 单视频 / UP 主空间 / 收藏夹 / 搜索批量 / 音频救援，自动保存为 SRT 格式。

## 统一入口

推荐使用统一入口：它支持单视频、收藏夹 URL 和 UP 主空间 URL。视频来源会写入
`data/videos_manifest.json`，任务状态写入 `data/subtitle_tasks.db`。

```powershell
# 单视频
python scripts\cli.py --video BVxxxxxxxxxx

# 收藏夹
python scripts\cli.py --favorite-url "https://space.bilibili.com/30210365/favlist?fid=3745603465&ftype=create"

# UP 主空间
python scripts\cli.py --space-url "https://space.bilibili.com/30210365/upload/video"

# 启用无原生字幕时的 ASR 兜底
python scripts\cli.py --favorite-url "..." --asr-fallback --asr-model small

# 重试失败或指定状态
python scripts\cli.py --favorite-url "..." --retry-failed
python scripts\cli.py --favorite-url "..." --only-status paused --retry-paused
```

旧的 `subtitle_extractor.py`、搜索批处理和单条救援入口仍保留，见下文。
> **🆕 v1.3 仓库自带 `extract_meta.py`（vendor 自 bilibili-video-meta），零外部依赖即可自动抓取视频元数据（播放量 / 标题 / 简介 / 点赞量 / 上传时间）并写入字幕顶端 frontmatter。**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.7+](https://img.shields.io/badge/python-3.7+-blue.svg)](https://www.python.org/downloads/)

---

## 特性

- 🎯 **五种能力**：单视频字幕 / UP 主空间批量 / 收藏夹批量 / 搜索批量 / 单条音频救援
- 🌐 **浏览器网络监听**：在登录态浏览器中监听 AI 字幕响应，稳定可靠
- 📁 **自动保存到 SRT 格式**（`.md` 扩展名，与项目约定一致）
- 🆕 **v1.3 零依赖元数据**：仓库自带 `extract_meta.py`（vendor 自 [bilibili-video-meta](https://github.com/xue1long/bilibili-video-meta) v1.2），无需安装额外 skill
- 🆕 **v1.3 自动拼入 frontmatter**：下载字幕后自动抓取 6 字段元数据（标题 / UP 主 / 播放量 / 点赞数 / 简介 / 上传时间）作为 YAML frontmatter 写到字幕文件顶端
- 🔁 **幂等写入**：重复执行不会重复堆叠 frontmatter
- 🛡 **容错设计**：元数据抓取失败不影响字幕主流程

---

## 工作流程

```
用户需求 → bilibili-subtitle-fetch → [单视频/UP主空间/收藏夹]
                                               ↓
                                  Playwright 持久化 profile
                                               ↓
                                  AI 字幕响应 → SRT 内容
                                               ↓
                           🆕 v1.2: 从 videoData 生成元数据
                                               ↓
                                      `.md` 字幕文件
```

---

## 安装

### 前置条件

- Python 3.7+
- Google Chrome 浏览器（已登录 B 站账号）
- `playwright`（字幕抓取默认后端；Selenium 仅作兼容回退）：

```bash
# 推荐先建 venv 隔离，再安装依赖
pip install -r requirements.txt        # 仓库自带依赖清单（含 playwright/selenium/yt-dlp/faster-whisper/pytest）
# 仅装最小依赖（等价）：
pip install playwright selenium webdriver-manager

# ⚠️ 必做：下载 Playwright 浏览器二进制（只装 pip 包不够，否则后端起不来）
playwright install chromium
```

空间模式还需要 `yt-dlp`；音频救援需要 `faster-whisper` 和系统 `ffmpeg`。

默认 Chrome profile 是项目根目录的 `.chrome-bilibili`；可用
`BILIBILI_SFETCH_CHROME_PROFILE` 覆盖。首次登录可运行
`python scripts/open_playwright_login.py`。

> 📌 完整初始化流程（venv / 登录态 profile / 跳过首次向导）见 [SETUP.md](SETUP.md)。

### 🆕 v1.3 元数据功能

v1.3 起，元数据抓取脚本 **`extract_meta.py` 已 vendor 进本仓库**（`scripts/extract_meta.py`），**无需任何外部 skill 依赖**。

如果你的环境中也已安装 [`bilibili-video-meta`](https://github.com/xue1long/bilibili-video-meta) 兄弟 skill，字幕下载会优先使用 bundled 副本（v1.3），不会影响独立使用 `bilibili-video-meta` 做其他事情。

---

## 使用方式

### 单视频字幕

```bash
python scripts/subtitle_extractor.py BV1xxxxxxxxxx
python scripts/subtitle_extractor.py "https://www.bilibili.com/video/BV1xxxxxxxxxx"
```

### UP 主空间批量字幕

```bash
python scripts/subtitle_extractor.py --space "https://space.bilibili.com/3546663834618256/upload/video"
```

### 收藏夹批量字幕

```bash
python scripts/subtitle_extractor.py --favorites
```

旧入口的 `--favorites` 读取项目根目录的 `videos_fav.json`；新任务应直接使用上面的
`--favorite-url`。

### 🆕 v1.2 元数据开关

```bash
# 关闭元数据 frontmatter（保留纯净 SRT）
python scripts/subtitle_extractor.py BV1xxxxxxxxxx --no-meta
```

### 自定义输出目录

```bash
python scripts/subtitle_extractor.py BV1xxxxxxxxxx --output /path/to/output/
```

### 搜索并批量下载

```bash
pip install playwright
python scripts/fetch_search_bvids.py "关键词" 20
python scripts/run_subtitle_batch.py
```

搜索模式默认复用 Chrome profile；运行前请关闭正在使用该 profile 的 Chrome 窗口。搜索需要 Edge 时可传入 `--browser edge`；批处理需要旧 Selenium Edge 流程时使用位置参数 `edge --backend selenium`。

### 风控与恢复

默认每条视频间隔 8–15 秒，连续 2 次观测到 412/429 后暂停 15 分钟。可按需调整：

```bash
python scripts/run_subtitle_batch.py --min-delay 8 --max-delay 15 \
  --rate-limit-threshold 2 --cooldown-seconds 900
```

登录失效会返回 `LOGIN_REQUIRED` 并停止；浏览器/页面启动失败最多重试一次。批处理退出码为：`0` 正常、`10` 限流暂停、`11` 需要登录、`12` 浏览器启动失败。熔断状态保存在输出目录的 `.bili_guard_state.json`，等待 `retry_after` 后重跑即可；已成功记录会跳过，未访问的视频保持待处理。

批处理健康路径复用一个浏览器实例，并在批处理结束时关闭一次；浏览器启动或页面加载失败最多重建一次。登录失败和限流不重建浏览器；无论成功、失败还是 CLI 提前退出，浏览器都会走幂等关闭路径。

### 监控输出契约

搜索结果的 `title_source` 只有 `card`（搜索卡片标题）、`detail`（详情页补取）和 `unresolved`（两者都没有）。批处理结果的每条记录都包含 `probe` 和 `duration_sec`：

```json
{
  "probe": {
    "request_observed": false,
    "response_status": null,
    "payload_received": false,
    "subtitle_count": 0,
    "language_count": 0,
    "hook_installed": false,
    "page_ready": false,
    "button_found": false,
    "click_dispatched": false,
    "request_count": 0
  },
  "duration_sec": 1.234
}
```

`SUBTITLE_NOT_OBSERVED` 表示没有观察到字幕请求；`NO_SUBTITLE` 表示请求成功但没有字幕；`SUBTITLE_API_FAILED` 表示请求失败或状态未知；`LOGIN_REQUIRED` 表示登录态不可用；`RATE_LIMITED` 表示实际观测到 412/429。只有连续达到阈值的 412/429 才打开全局熔断，普通字幕缺失只记录为当前视频失败，不暂停后续视频。

监控只保存上述结构化证据，不持久化 cookies、Authorization headers、响应 body、页面文本或原始响应 URL。

项目不处理 CAPTCHA，不轮换代理/IP，也不伪装浏览器指纹。

### 单条音频救援

```bash
pip install faster-whisper playwright
python scripts/rescue_one_subtitle.py BV1xxxxxxxxxx --model small
```

系统需要可执行的 `ffmpeg`。可通过 `BILIBILI_SFETCH_ROOT`、`BILIBILI_SFETCH_CHROME_PROFILE`、`BILIBILI_SFETCH_EDGE_PROFILE`、`BILIBILI_SFETCH_CHROMEDRIVER` 和 `BILIBILI_SFETCH_CHROME_BIN` 覆盖默认路径。

---

## 输出格式

文件默认保存到 `10_raw/01_B站视频转录/{BV号}.md`（可用 `--output` 覆盖）：

### 🆕 v1.3+ 输出示例

```markdown
---
title: 你在纠结选 Codex 还是 Hermes？这个问题本身就问错了
uploader: 王帅真
view_count: 1537
like_count: 32
video_published_at: 2026-06-10
bvid: BV1SYES6BEPW
url: https://www.bilibili.com/video/BV1SYES6BEPW
description: |
  这期聊 Codex/CC 和 Hermes/OpenClaw 这类 AI Agent 到底该怎么选。

  主要内容：
  - 为什么 Codex 和 Hermes 不是简单的竞品关系
  - ...
---
1
00:00:00,000 --> 00:00:02,000
哈啰大家好，这里是王帅真

2
00:00:02,000 --> 00:00:04,800
今天这一期视频想跟大家来分享一下
...
```

### 元数据字段（6 字段）

| 字段 | 来源 | 说明 |
|------|------|------|
| `title` | `videoData.title` | 视频原标题 |
| `uploader` | `videoData.owner.name` | UP 主名 |
| `view_count` | `videoData.stat.view` | 播放量 |
| `like_count` | `videoData.stat.like` | 点赞数 |
| `description` | `videoData.desc` | 视频简介（含换行） |
| `video_published_at` | `videoData.pubdate` | 发布日期（YYYY-MM-DD） |

元数据优先来自已登录 Playwright 页面中的 `videoData`，由 bundled 的
`extract_meta.py`（vendor 自 [`bilibili-video-meta`](https://github.com/xue1long/bilibili-video-meta) v1.2）解析；失败时只跳过 enrichment，不影响字幕下载。

---

## 跳过逻辑（去重）

统一入口和旧入口使用不同的状态层：

| 层级 | 数据源 | 命中条件 |
|------|--------|----------|
| 1 | SQLite（统一入口） | `data/subtitle_tasks.db` 中状态为 `native_success` 或 `asr_success` |
| 2 | `download_list.json`（旧入口） | `{output_dir}/records[{BV号}].status == "success"` |

首次创建 `download_list.json` 时会扫描输出目录中的 `.md` 文件；`compile_db.json`
仅在存在对应 Wiki record 时补写字幕路径，不参与去重。

---

## 已知问题

1. **需要登录态**：AI 字幕和 ASR 救援都需要可用的 B 站 cookie
2. **profile 占用**：同一个持久化 profile 不能同时被多个浏览器进程使用
3. **单字幕优先**：多字幕时默认选第一个语言项
4. **Windows Unicode**：脚本以 UTF-8 输出；旧版控制台仍可能显示替换字符

---

## 依赖安装汇总

```bash
pip install -r requirements.txt
playwright install chromium         # ⚠️ 必做：下载浏览器二进制
# 另需系统 ffmpeg（音频救援）
```

---

## 相关项目

- [`bilibili-video-meta`](https://github.com/xue1long/bilibili-video-meta) — 视频元数据独立 skill（v1.3 起核心代码已 vendor 进本仓库；如需独立抓元数据，可单独安装）
- [video-wiki-compile](https://github.com/) — 视频笔记 Wiki 编译流水线

---

## 文件结构

```
bilibili-subtitle-fetch/
├── AGENT.md                       # Agent 项目约定
├── SKILL.md                       # 字幕抓取 skill
├── README.md                      # 本文件
├── SETUP.md                       # Windows/PowerShell 初始化
├── requirements.txt               # Python 依赖
├── LICENSE                        # MIT
├── .gitignore
└── scripts/
    ├── cli.py                     # 🆕 统一入口（单视频 / 收藏夹 URL / UP 空间）
    ├── subtitle_extractor.py      # 兼容入口（默认 Playwright 监听字幕；Selenium 兼容回退）
    ├── open_playwright_login.py   # 打开登录窗口，写入项目专用 profile（.chrome-bilibili）
    ├── extract_meta.py            # 🆕 v1.3 vendor：抓视频元数据（自 bilibili-video-meta v1.2）
    ├── prepend_meta.py            # 🆕 v1.2：把元数据拼成 frontmatter 写到字幕顶端
    ├── fetch_search_bvids.py      # 🆕 搜索结果抓 BV 号
    ├── run_subtitle_batch.py      # 🆕 批量下载字幕
    ├── rescue_one_subtitle.py     # 🆕 音频 ASR 救援
    ├── runtime_paths.py           # 运行时路径解析
    ├── backends/                  # 抓取后端：playwright_subtitle / asr_rescue
    ├── pipeline/                  # 批处理流水线
    ├── sources/                   # 视频源解析（空间 / 收藏夹 / 搜索）
    └── storage/                   # manifest / SQLite / 旧 JSON 迁移
```

运行时生成的 `data/`、`10_raw/`、`.batch/`、`.chrome-bilibili/` 和临时音频不应提交。

---

## 版本历史

- **v1.3** (2026-06-10) — Vendor `extract_meta.py`，零外部依赖
- **v1.8** (2026-09-21) — 统一来源、任务规划和 SQLite 状态库；保留旧入口兼容
- **v1.2** (2026-06-10) — 新增"下载字幕后自动拼元数据 frontmatter"功能
- **v1.1** — 移除对 karpathy `videos.db` 的依赖，保留对 `compile_db.json` 的兼容写回
- **v1.0** — 初始发布：单视频 / UP 主空间 / 收藏夹三种模式

---

## License

MIT — 详见 [LICENSE](LICENSE) 文件。

---

## 致谢

- B 站浏览器扩展的 XHR/Fetch Hook 思路
- [`bilibili-video-meta`](https://github.com/xue1long/bilibili-video-meta) 的元数据抓取能力（v1.3 起核心代码 vendor 自该项目）
