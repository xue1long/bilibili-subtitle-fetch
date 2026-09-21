---
name: bilibili-subtitle-fetch
description: B站AI字幕下载工具，支持单视频、UP主空间、收藏夹、**搜索+批量**、**单条救援（音频ASR）**五种模式提取字幕。字幕主流程默认使用 Playwright 持久化浏览器并监听 AI 字幕响应，自动保存为 SRT 文件。Selenium 保留为兼容回退。**🆕 v1.6 救援模式**：单个 BV 号走 DASH audio stream + faster-whisper 本地 ASR 转录路径。**🆕 v1.5 下载列表去重**：download_list.json 跟踪下载状态，成功/失败全程记录。**🆕 v1.3 仓库自带 extract_meta.py（vendor 自 bilibili-video-meta），零外部依赖即可自动抓取视频元数据并写入字幕顶端 frontmatter。** 当用户说"下载字幕"、"提取字幕"、"导出字幕"、"AI字幕"、"BV号字幕"、"搜索+下载"、"批量搜索"、"用音频转录救一下"时触发。收藏夹模式需先运行 update-bilibili-favorites 更新视频列表。
context: fork
agent: general-purpose
---

# Bilibili AI Subtitle Fetch

B站AI字幕下载工具，支持五种模式：
1. **单视频字幕** — 输入BV号或视频URL
2. **UP主空间字幕** — 输入UP主上传视频页面URL，批量下载
3. **收藏夹字幕** — 读取已有 `videos_fav.json`，批量下载收藏夹视频字幕
4. **🆕 v1.5 搜索 + 批量下载** — 输入关键词，从 B站搜索结果抓 N 个 BV 号，再逐条下载字幕
5. **🆕 v1.6 救援模式** — 读 `download_list.json` 的 `status=failed` 记录，下载音频 → 本地 ASR 转录 → 写 SRT（不走 JS Hook）

## 工作流程

```dot
digraph {
  rankdir=TB;
  user[label="用户需求", shape=oval];
  skill[label="bilibili-subtitle-fetch", shape=box"];
  single[label="单视频字幕", shape=box];
  space[label="UP主空间字幕", shape=box];
  fav[label="收藏夹字幕", shape=box];
  search[label="v1.5 搜索+批量", shape=box];
  hook[label="Playwright监听\nAI字幕响应", shape=box];
  srt[label=".srt 字幕文件", shape=cylinder];
  meta[label="🆕 v1.2 自动拼元数据", shape=box, style=filled, fillcolor=lightyellow];
  metaFetch[label="调用 bilibili-video-meta\n抓 6 字段", shape=box];
  frontmatter[label="prepend_meta.py\n拼 YAML frontmatter", shape=box];
  recList[label="download_list.json\nstatus=failed", shape=cylinder];
  rescue[label="🆕 v1.6 救援模式\n音频+ASR", shape=box, style=filled, fillcolor=lightblue];

  user -> skill;
  skill -> single -> hook -> srt -> meta;
  skill -> space -> hook -> srt -> meta;
  skill -> fav -> hook -> srt -> meta;
  skill -> search -> hook;
  recList -> rescue -> srt;
  meta -> metaFetch -> frontmatter;
  frontmatter -> srt[label="覆写"];
}
```

## 自动判断规则

| 用户需求 | 调用的模式 |
|---------|-----------|
| "下载BV号字幕"、"单视频字幕" | `single` 模式 |
| "下载空间字幕"、"UP主字幕"、"批量字幕" | `space` 模式 |
| "收藏夹字幕"、"全部字幕" | `fav` 模式 |
| **"搜索XX下载字幕"、"搜索100个视频"** | **`search+batch` 模式** |
| **"用音频转录救一下"、"救援这个 BV 号"** | **`rescue` 模式**（处理单个 BV 号） |

## 前置条件

- Python 3 + Playwright Chromium
- 项目专用 Playwright profile 中已登录 B站账号
- Selenium 和 ChromeDriver 仅用于 `--backend selenium` 兼容回退
- UP 主空间模式额外需要：`pip install yt-dlp`
- `search+batch` 模式额外需要：`pip install playwright` + Playwright Chromium（`playwright install chromium` 或用本地缓存 `%LOCALAPPDATA%/ms-playwright/`）
- 已运行 `update-bilibili-favorites`（收藏夹模式必需）

## 使用方式

### 方式一：命令行

```bash
# 单视频字幕
python scripts/subtitle_extractor.py BV11RffBdEEQ --browser chrome

# UP主空间字幕
python scripts/subtitle_extractor.py --space "https://space.bilibili.com/3546663834618256/upload/video"

# 收藏夹字幕
python scripts/subtitle_extractor.py --favorites
```

### 方式三：🆕 v1.5 搜索 + 批量下载

**两阶段：先抓 BV 号，再逐条下字幕。**

```bash
# Stage 1: 搜索 "hyperframes"，抓 100 个 BV 号
python scripts/fetch_search_bvids.py hyperframes 100
# 默认 output_dir = <project_root>/10_raw/01_B站视频转录
# 产物: <output_dir>/.batch/hyperframes_100_bvids.json

# Stage 2: 读 BV 号 JSON，逐条调字幕下载
python scripts/run_subtitle_batch.py
# 默认: 读最近一次 Stage 1 产物
# 产物: <output_dir>/<BV>.md + .batch/<keyword>_results.json
```

**实测**：100 条约 30-50 分钟，具体取决于页面加载和限流；B 站很多视频没有 AI 字幕。

**典型用法（本项目 LLM_Knowledge_base_v2）**：
```bash
# 搜索 + 下载 100 条 hyperframes 相关视频
python 40_scripts/compile/fetch_search_bvids.py hyperframes 100
python 40_scripts/compile/run_subtitle_batch.py
# 40_scripts/compile/ 下两个脚本是 thin wrapper，转发到 skill 的同名脚本
```

**关键设计**：
- Stage 1 用 Playwright（不是裸 API）—— B 站 2023 后对搜索 API 加 wbi 风控，裸 HTTP 拿不到完整结果
- Stage 2 默认使用 Playwright；需要旧 Selenium 流程时显式传入 `--backend selenium`
- download_list.json 自动去重（与 single/space/fav 模式共享同一份）

**已知限制**：
- 搜索结果前几条 title 可能是搜索页 player 预览的 UI 文本（不是真实视频标题），BV 号本身有效
- 部分视频 B 站右侧"稍后再看"栏的 BV 也会被误抓进来（BV 有效但可能跟关键词无关）
- 无 AI 字幕的视频会失败（这是 skill 固有限制，不是 bug）

### 方式四：🆕 v1.6 救援模式（音频 ASR fallback）

JS Hook 模式失败后（典型错误 `Chrome failed to start: crashed`，或视频本身无 AI 字幕）的兜底路径：**不走 JS Hook，改走 DASH audio stream + 本地 ASR**。

单条救援直接处理一个 BV 号；成功或失败都会更新输出目录中的 `download_list.json`。

```bash
# 单条救援
python scripts/rescue_one_subtitle.py BV127LX6vESD
# --model small  切小模型加速（默认 large-v3）
# --keep-audio  保留临时音频（debug 用）

```

**完整流程**详见下方"流程说明 → 救援模式流程"。

## 输出格式

- 文件名：`{BV号}.md`（SRT 时间轴格式存为 .md）
- 位置：`10_raw/01_B站视频转录/`（可通过 `--output` 覆盖）
- 格式：
  - **v1.2+ 字幕顶端带 YAML frontmatter**（自动抓取的 6 字段元数据，详见下文）
  - SRT 时间轴 + 中文/英文/双语字幕内容（内容格式不变，仅扩展名改为 .md）

## 🆕 v1.2 元数据自动拼入

下载字幕成功后，**自动**调用仓库内的 `extract_meta.py` 抓取视频元数据，拼成 YAML frontmatter 写到字幕文件顶端。无需二次调用。

**写入字段**（6 字段）：

| 字段 | 来源 | 示例 |
|------|------|------|
| `title` | `videoData.title` | 你在纠结选 Codex 还是 Hermes？... |
| `uploader` | `videoData.owner.name` | 王帅真 |
| `view_count` | `videoData.stat.view` | 1491 |
| `like_count` | `videoData.stat.like` | 28 |
| `video_published_at` | `videoData.pubdate` (Unix → 日期) | 2026-06-10 |
| `description` | `videoData.desc` | 这期聊 Codex/CC 和 Hermes/OpenClaw... |

**示例 frontmatter**（拼到字幕文件最顶部）：

```yaml
---
title: 你在纠结选 Codex 还是 Hermes？这个问题本身就问错了
uploader: 王帅真
view_count: 1491
like_count: 28
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
...
```

**实现细节**：

- **v1.3 零依赖**：元数据抓取脚本 `extract_meta.py` 已 vendor 进本仓库（`scripts/extract_meta.py`），无需安装任何外部 skill
- **路径查找顺序**：bundled (`scripts/extract_meta.py`) 优先 → 外部 (`../bilibili-video-meta/scripts/extract_meta.py`) 回退 → 都找不到则跳过
- **best-effort enrichment**：元数据抓取失败时字幕下载主流程不受影响（只 print 警告，不抛错）
- **幂等写入**：若字幕文件已有 frontmatter，先剥离再插入；可重复执行不重复堆叠
- **原子写**：通过 `.tmp` 文件 + rename 写入，写失败时原文件保持原样
- **跳过字段**：description 留空时整段不写，避免空块

**关闭元数据拼入**（保留纯净 SRT 模式）：

```bash
python scripts/subtitle_extractor.py BV1xxx --no-meta
```

## 数据库状态更新（🆕 改写至 compile_db.json）

字幕下载成功后，更新本项目 wiki DB：

| 字段 | 说明 |
|------|------|
| `subtitle_srt_path` | 字幕文件路径（v1.1 新增） |
| `subtitle_downloaded_at` | 下载完成时间（v1.1 新增） |
| `id` 已存在但 record 缺失 | 跳过更新，让 wiki compile 流程处理 |

## 下载列表去重机制（v1.4 新增）

使用 `download_list.json` 实现下载状态跟踪，避免重复下载。

**列表文件**：`{output_dir}/download_list.json`

**结构**：
```json
{
  "created_at": "2026-06-15T10:00:00",
  "updated_at": "2026-06-15T12:30:00",
  "records": {
    "BV11RffBdEEQ": {
      "status": "success",
      "downloaded_at": "2026-06-15T10:05:00",
      "subtitle_path": "10_raw/01_B站视频转录/BV11RffBdEEQ.md"
    },
    "BV1SYES6BEPW": {
      "status": "failed",
      "downloaded_at": "2026-06-15T10:10:00",
      "error": "未捕获到字幕"
    }
  }
}
```

**初始化流程**：
1. 首次运行 → 扫描 `output_dir` 下所有 `.md` 文件，提取 BV 号，生成列表（status=success）
2. 后续运行 → 加载已有列表

**去重规则**：
- 每次下载前检查列表：status=success 已存在则跳过
- 下载成功后 → 更新列表 status=success + 时间 + 路径
- 下载失败后 → 更新列表 status=failed + 时间 + error

### 登录态、限速与熔断

- 运行前检查已登录 B 站账号；页面初始状态存在不等于登录成功，必须有账号入口等正向信号。
- 默认每条视频间隔 8–15 秒；连续 2 次实际观测到 412/429 后暂停 900 秒。
- 失败分类：`SUBTITLE_NOT_OBSERVED`、`NO_SUBTITLE`、`SUBTITLE_API_FAILED`、`LOGIN_REQUIRED`、`RATE_LIMITED`、`BROWSER_START_FAILED`、`PAGE_LOAD_FAILED`。
- 浏览器/页面启动失败最多重试一次；登录失败和 412/429 不立即重试。
- 搜索批处理健康路径复用一个浏览器实例并在结束时关闭一次；浏览器启动或页面加载失败最多重建一次，登录失败和限流不重建。所有入口退出时都会幂等关闭浏览器。
- `title_source` 只有 `card`、`detail`、`unresolved`，分别表示搜索卡片标题、详情页标题或仍未解析出标题；不得把播放量、时长、排名等指标当标题。
- 每条批处理记录都包含完整 `probe`：`request_observed`、`response_status`、`payload_received`、`subtitle_count`、`language_count`、`hook_installed`、`page_ready`、`button_found`、`click_dispatched`、`request_count`，以及数值型 `duration_sec`。
- `SUBTITLE_NOT_OBSERVED` 是未观察到字幕请求；`NO_SUBTITLE` 是请求成功但无字幕；`SUBTITLE_API_FAILED` 是请求失败或状态未知；`LOGIN_REQUIRED` 是登录态不可用；`RATE_LIMITED` 是实际 412/429。只有 412/429 才计入全局熔断，普通字幕缺失保持逐视频失败。
- 监控不持久化 cookies、Authorization headers、响应 body、页面文本或原始响应 URL。
- `.bili_guard_state.json` 保存限流熔断控制状态；批处理退出码：`0` 正常、`10` 限流暂停、`11` 需要登录、`12` 浏览器启动失败。
- 批处理暂停时不标记未访问视频为失败；重跑会跳过已成功视频，并遵守 `retry_after`。
- 不处理 CAPTCHA，不轮换代理/IP，不伪装浏览器指纹。

**日志示例**：
```
[下载列表] 已加载 5 条记录: download_list.json
[跳过] BV11RffBdEEQ - 下载列表中已存在
```

## 核心脚本

| 脚本 | 说明 |
|------|------|
| `subtitle_extractor.py` | 主脚本，默认使用 Playwright，支持单视频/UP主空间/收藏夹，保存后自动处理元数据 |
| `prepend_meta.py` | 🆕 v1.2 把 bilibili-video-meta 的 JSON 输出拼成 YAML frontmatter 写到字幕顶端 |
| `fetch_search_bvids.py` | 🆕 v1.5 Playwright 抓 B站搜索结果 BV 号，写到 .batch/<keyword>_<n>_bvids.json |
| `run_subtitle_batch.py` | 🆕 v1.5 读 BV 号 JSON，in-process 批量调 subtitle_extractor 跑每条视频 |
| `_driver_patch.py` | 🆕 v1.5 selenium.webdriver.Edge 的 monkey-patch，受限网络下用本地 chromedriver + Playwright chromium 顶替 msedgedriver |
| `rescue_one_subtitle.py` | 🆕 v1.6 单条救援 CLI：playwright + Chrome profile → DASH API → ffmpeg → faster-whisper ASR |
| `subtitle_rescue.py` | 🆕 v1.7 可复用 ASR 救援函数，供主流程和 CLI 使用 |

## 已知问题

1. **Playwright profile 占用**：同一个持久化 profile 不能同时被多个浏览器进程使用；关闭占用该 profile 的窗口后重试
2. **需要登录态**：AI字幕需B站账号登录，未登录只能获取普通字幕；救援模式同样需要 Chrome profile 里有 B站 cookie 才能过 412 风控
3. **单字幕优先**：多字幕时默认选第一个语言项
4. **Windows Unicode**：打印Unicode符号可能报错，脚本已处理
5. **救援模式无 AI 字幕**：纯音乐 / 屏保 / 无语音视频 ASR 会得到空段或乱码（这种原本也不该出现在 failed 列表里）

## 依赖安装

```bash
# 基础（单视频/UP主空间/收藏夹）
pip install playwright
playwright install chromium

# Selenium 兼容回退（可选）
pip install selenium webdriver-manager

# 搜索 + 批量模式额外需要
pip install playwright
playwright install chromium   # 或用本地已有缓存

# 🆕 v1.6 救援模式额外需要
pip install faster-whisper    # CTranslate2 加速的 Whisper 本地推理
# 还需要系统装了 ffmpeg（PATH 里能调起 `ffmpeg` 命令）
```

**受限网络 / msedgedriver CDN 不可达时**：
可用 `_driver_patch.py` 走本地 chromedriver；路径不再写死，按需设置：

```bash
# Windows PowerShell
$env:BILIBILI_SFETCH_CHROMEDRIVER = "D:\path\to\chromedriver.exe"
$env:BILIBILI_SFETCH_CHROME_BIN = "D:\path\to\chrome.exe"
python ...scripts/run_subtitle_batch.py
```

还可设置 `BILIBILI_SFETCH_ROOT`、`BILIBILI_SFETCH_CHROME_PROFILE` 或 `BILIBILI_SFETCH_EDGE_PROFILE` 覆盖项目根目录与浏览器 profile。普通字幕流程默认使用 `BILIBILI_SFETCH_CHROME_PROFILE`；可用 `--backend selenium` 切回 Selenium。

## 流程说明

### 单视频字幕流程

1. 启动 Playwright 持久化浏览器（加载项目 profile）
2. 打开视频页面，等待播放器加载
3. 点击字幕按钮，选择第一个语言项
4. 监听 `aisubtitle.hdslb.com` / `ai_subtitle` 响应并解析 `body`
5. 转换为 SRT 格式，保存文件
6. 更新 `download_list.json` 和数据库路径

### UP主空间字幕流程

1. 通过 yt-dlp 提取该空间下所有视频 BV 号
2. 循环调用单视频字幕流程
3. 增量保存（已下载的字幕跳过）

### 收藏夹字幕流程

1. 读取 `videos_fav.json`
2. 循环调用单视频字幕流程
3. 增量保存（已下载的字幕跳过）

### 🆕 v1.5 搜索 + 批量下载流程

1. **`fetch_search_bvids.py`（Stage 1：抓 BV 号）**
   - 默认启动本地 Chrome 浏览器（`channel='chrome'`，复用系统登录态）；可用 `--browser edge` 显式切换
   - 打开 `search.bilibili.com/all?keyword=<kw>&page=<n>`，等候 video card 渲染
   - 抓所有 `a[href*='/video/BV']` 元素，提取 BV 号 + 标题
   - 翻页到抓满 `target` 个唯一 BV 号为止（每页 20 条）
   - 输出 `<output_dir>/.batch/<keyword>_<target>_bvids.json`

2. **`run_subtitle_batch.py`（Stage 2：批量下字幕）**
   - 调 `_driver_patch.patch_selenium_edge()` 替换 driver
   - in-process `import subtitle_extractor`，构造 `SubtitleExtractor`
   - 对每个 BV 调 `extractor.extract_single(bvid)`，捕获 stdout/stderr 判定 success/skipped/failed
   - 批量构造 `SubtitleExtractor(reuse_browser=True)`，结束时关闭共享浏览器
   - 实时打印进度 + ETA，最终写 `<output_dir>/.batch/<keyword>_results.json`

### 🆕 v1.6 救援模式流程

**适用**：JS Hook 模式失败（典型 `Chrome failed to start: crashed`），或视频本身无 AI 字幕。

**核心思路**：放弃 JS Hook 拦截 AI 字幕，改为下载 DASH audio stream → 本地 ASR 转录。

### 统一入口与状态库（v1.8）

单视频、收藏夹 URL 和 UP 主空间统一使用 `scripts/cli.py`。来源适配器位于
`scripts/sources/`，任务规划位于 `scripts/pipeline/`，字幕后端位于
`scripts/backends/`。

```powershell
python scripts/cli.py --video BVxxxxxxxxxx
python scripts/cli.py --favorite-url "https://space.bilibili.com/.../favlist?fid=..."
python scripts/cli.py --space-url "https://space.bilibili.com/.../upload/video"
python scripts/cli.py --favorite-url "..." --asr-fallback --asr-model small
python scripts/cli.py --favorite-url "..." --retry-failed
```

来源清单保存到 `data/videos_manifest.json`，任务状态保存到
`data/subtitle_tasks.db`。`download_list.json` 保留为兼容输出。

**单条（`rescue_one_subtitle.py`）**：

1. 用户先关闭正在使用同一 Chrome profile 的 Chrome 实例
2. Playwright 默认启动 Chrome 持久化 profile（`launch_persistent_context`）—— 复用系统登录态绕过 412 风控
3. 打开 B站视频页，等 `networkidle`
4. 注入 `FETCH_DASH_JS` —— 从 `window.__INITIAL_STATE__.videoData` 抓 6 字段元数据（同时给 frontmatter 用）；调 `/x/player/playurl?fnval=16&fourk=1&qn=80` 拿 audio stream URL；如无 audio stream，回退到 video stream 直接喂 ffmpeg
5. **Python urllib 下载 m4s**（不在浏览器里 fetch）—— CDN 跨域限制必须在浏览器外下载，带 `Referer: https://www.bilibili.com` + UA 头
6. ffmpeg 转 mp3（`libmp3lame 64k`）
7. faster-whisper 转录（`language=None` 自动检测；默认 `large-v3`）
8. 写 SRT 文件 `10_raw/01_B站视频转录/{BV}.md`
9. 复用 `prepend_meta.py` 拼 frontmatter（meta 用 `bv` 字段名）
10. 更新 `download_list.json` —— status=success/failed + downloaded_at + error

**已知限制**：

- ASR 转录质量取决于视频人声清晰度；纯音乐 / 屏保 / 无语音视频会得到空段或乱码
- faster-whisper large-v3 首次加载 ~5-10s（吃 RAM ~3GB）；small 模型 ~1s（~1GB RAM）
- 仍受 B站 412 风控（Playwright 复用登录态绕过，但 Chrome profile 必须有 B站 cookie）

## 注意事项

- 字幕下载需要 B站登录态，Cookie 过期会导致失败
- 批量下载建议加延时，避免请求过快被限频
- 字幕路径会写入 `compile_db.json` 的 `subtitle_srt_path` 字段，便于后续 wiki compile 流程读取
- 如果视频没有 AI 字幕，脚本会提示但不会报错
- v1.6 救援模式走的是音频 ASR 转录路径，与 JS Hook 模式独立；同一条 BV 两个模式可以各跑一次（产物以最近一次为准）
