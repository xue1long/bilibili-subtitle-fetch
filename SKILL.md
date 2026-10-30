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
| 抖音图文帖 | 同上命令，自动识别为图文并下载图片（`--image-output` 指定目录） |
| 小红书视频/图文 | `python scripts/xiaohongshu_cli.py --url "https://www.xiaohongshu.com/explore/<笔记ID>?xsec_token=...&xsec_source=pc_user&source=web_profile_page"` |

推荐使用 `cli.py`：它发现来源、写入 manifest，并用 SQLite 规划可恢复任务。任务是否"已产出"同时看**产物文件是否存在**——多台电脑共用同一份产物根时，靠它天然跨机去重，不必共享任务库。旧入口仍保留 `--space`、`--favorites`、`--output`、`--no-meta`、`--backend` 等选项；`--favorites` 读取项目根目录的 `videos_fav.json`。

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
- **产物根**默认是仓库内的 `10_raw/`；设 `BILIBILI_SFETCH_RAW_DIR` 可改指向别处，跨机共享时指向同步盘。值可以写**未展开**的 `%OneDriveConsumer%\...`，由每台机器展开成自己的盘符，同一份配置在所有机器上都成立。详见 `docs/cross-machine-sharing.md`。
- 来源：`data/videos_manifest.json`。
- 统一任务状态主库：`data/subtitle_tasks.db`。
- 旧入口、批处理和救援共用：`{output_dir}/download_list.json`；CLI 会从 `data/subtitle_jobs.json` 和该文件迁移旧记录。
- 限流状态：`{output_dir}/.bili_guard_state.json`。
- `scripts/compile_db.json` 只在存在对应 Wiki record 时补写 `subtitle_srt_path`，不承担任务状态。

成功字幕会尽力写入 YAML frontmatter。Playwright 优先从当前已登录页面的 `window.__INITIAL_STATE__.videoData` 读取元数据，避免额外无 Cookie 请求；读取失败不影响字幕主流程。使用 `--no-meta` 关闭。

## 抖音单条（视频 / 图文）

`douyin_cli.py` 先用无 Cookie 临时 Playwright 页面打开 `https://www.douyin.com/video/<视频ID>`，抓取 `aweme/v1/web/aweme/detail` 接口的 JSON 响应。接口按 `aweme_id` 精确关联目标帖，因此不会拿到页面上别人的媒体；抖音的 `user/self?modal_id=` 分享链接也会先归一化成详情页地址。页面提示登录不等于不能下载。匿名解析失败后才回退旧页面扫描、`yt-dlp`，最后才使用 `.chrome-douyin` 登录 profile。

两点容易踩：抖音是客户端渲染，**初始 HTML 里没有 `playAddr`**（正则命中 0 次），媒体只能从 detail 接口拿；而且该接口在页面加载后约 **8 秒**才返回，所以要等事件而不是固定 sleep。

按帖子类型分两条路：

| 类型 | 判定 | 产物 |
|---|---|---|
| 视频 | `aweme_type` 0 / `media_type` 4 | `10_raw/02_抖音视频转录/DY<视频ID>.md`，含 `platform`、`video_id`、`title`、`uploader`、`description`、`url`、`transcript_model`；播放地址取 `video.bit_rate[].play_addr.url_list` |
| 图文 | `aweme_type` 68 / `media_type` 2 | `10_raw/05_抖音笔记图片/DY<视频ID>/`，内含 `01.webp`… 与 `DY<视频ID>.md`，含 `note_type: image`、`asset_count`、`assets`；正文是 `![图 N](01.webp)` 图片引用，Obsidian 直接可看；图片取 `images[].download_url_list`，不做语音转录 |

图文帖的 `video.play_addr` 指向背景音乐而不是内容本身，解析时必须排除。

```powershell
python scripts/douyin_cli.py --url "https://www.douyin.com/user/self?modal_id=7687559858779351972&showTab=favorite_collection"
python scripts/douyin_cli.py --url "https://www.douyin.com/video/7687559858779351972" --image-output "10_raw\05_抖音笔记图片"
```

## 小红书视频和图文

`xiaohongshu_cli.py` 支持 `/explore/<笔记ID>` 和 `/board/<笔记ID>` 单条笔记；请传入包含 `xsec_token`、`xsec_source`、`source` 的完整分享 URL —— **链接必须带 token 才能打开笔记**。

发现资源走「普通 Chrome + CDP」通道：用 `scripts/xiaohongshu_cdp.py` 以**普通方式**启动 Chrome（只加 `--remote-debugging-port`，不带任何自动化参数），再让 Playwright 通过 CDP 附着上去。原因：Playwright 自己启动的 Chromium 必带 `--enable-automation`，`navigator.webdriver` 为 true，小红书风控会把笔记页跳到登录窗口；而 Chrome 136+ 又禁止在默认 user-data-dir 上开远程调试端口，所以要用独立 profile 目录承载。Chrome 以 **headless 优先**启动（不弹窗口、也不会被跳登录页），拿不到再回落有头模式。

页面先弹登录窗口、后台仍在加载笔记是正常现象：流程会等页面状态里的 `imageList` 就绪再判定，取不到才算 `LOGIN_REQUIRED`。**普通 Chrome 不需要登录态也能读到笔记**（拦的是浏览器的 automation 标记，不是会话），所以正常情况下不必扫码。

取图有两处容易出错：

- 权威图集是 `window.__INITIAL_STATE__.note.noteDetailMap[<笔记ID>].note.imageList`，DOM 兜底会漏掉懒加载的轮播图（实测只拿到一半）；页面右上角的 `N/M` 计数器就是权威张数，可与 `imageList` 长度交叉核对。
- `imageList[].urlDefault` 是 `http://` 明文，必须升级成 `https://`，否则 CDN 返回 403。

图文笔记保存为按原比例缩小 50% 的 `images/*.webp`，同目录 `<笔记ID>.md` 保存 YAML 元数据、相对资源清单，并在正文写图片引用 `![图 N](images/01.webp)`，Obsidian 直接可看。

**视频笔记不保存视频文件。** 视频只落在项目本地的媒体暂存区 `.scratch/`，ASR 转录成功后立即删除，产物根里不留任何 `.mp4`——产物根可能在同步盘上，媒体进去会被完整上传一遍再删除。视频笔记的产物形态与抖音、B站一致：正文是语音转录文本，带 `transcript_model`。已存在的旧笔记不回填正文，只对新下载的笔记生效。

```powershell
python scripts/xiaohongshu_cli.py --url "https://www.xiaohongshu.com/explore/<笔记ID>?xsec_token=...&xsec_source=pc_user&source=web_profile_page"
```

默认目录是 `10_raw/03_小红书/<笔记ID>/`；可用 `--output` 覆盖，已有目录需显式追加 `--overwrite`。默认 profile 是 `.chrome-xiaohongshu`，可用 `XHS_SFETCH_CHROME_PROFILE` 覆盖；CDP 通道用的 profile 可用 `XHS_SFETCH_CDP_PROFILE` 单独指定。签名 URL 不落盘，不处理 CAPTCHA、付费限制或 DRM。

若返回 `LOGIN_REQUIRED`，先运行 `python scripts/open_xiaohongshu_login.py`，完成一次人工登录后再重试。

不要试图复用用户日常 Chrome 的登录态：Chrome 运行时会把 `Default/Network/Cookies` 独占锁住，`shutil.copy2` 报 PermissionError、SQLite 读不了、`esentutl` 报 `JET_errFileAccessDenied(-1032)`。CDP 通道用项目自己的 `.chrome-xiaohongshu` profile 即可。

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
- `scripts/xiaohongshu_media.py` / `scripts/xiaohongshu_cli.py`：小红书视频和图文媒体发现、下载与元数据清单。
- `scripts/xiaohongshu_cdp.py`：以普通方式启动 Chrome 并暴露 CDP，供 Playwright 附着读取。
- `scripts/open_xiaohongshu_login.py`：初始化小红书持久化登录 profile。

修改抓取、状态或风控逻辑后运行：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```
