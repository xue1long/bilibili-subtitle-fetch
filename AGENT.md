# Agent Guide

这是一个用 Playwright 抓取 B 站 AI 字幕、并可用本地 ASR 救援的 Python 工具。

## 入口与结构

- `scripts/cli.py`：推荐入口，支持单视频、收藏夹 URL、UP 主空间 URL，以及任务重试。
- `scripts/subtitle_extractor.py`：兼容旧入口；默认 Playwright，可用 `--backend selenium` 回退。
- `scripts/fetch_search_bvids.py` / `scripts/run_subtitle_batch.py`：搜索并批量下载。
- `scripts/rescue_one_subtitle.py`：单条视频的 DASH 音频 + faster-whisper 救援。
- `scripts/douyin_cli.py`：抖音单条下载，视频走音频提取 + faster-whisper 转录，图文走图片下载 + 元数据清单。
- `scripts/xiaohongshu_cli.py`：小红书单条视频/图文下载和元数据清单。
- `scripts/open_xiaohongshu_login.py`：初始化小红书持久化登录 profile。
- `scripts/sources/`：视频来源适配器。
- `scripts/pipeline/`：任务模型和去重规划。
- `scripts/backends/`：Playwright 字幕与 ASR 后端。
- `scripts/storage/`：manifest、SQLite 状态库和旧 JSON 迁移。
- `tests/`：单元测试与入口/浏览器生命周期测试。
- `10_raw/01_B站视频转录/`：默认字幕输出目录。
- `10_raw/02_抖音视频转录/`：抖音视频字幕输出目录。
- `10_raw/05_抖音笔记图片/DY<视频ID>/`：抖音图文图片和 `<视频ID>.md` 输出目录；`.md` 正文是 markdown 图片引用。
- `10_raw/03_小红书/<笔记ID>/`：小红书媒体和 `<笔记ID>.md` 输出目录；`.md` 正文写 markdown 资源引用。

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
python scripts\douyin_cli.py --url "https://www.douyin.com/video/<视频ID>" --image-output "10_raw\05_抖音笔记图片"
python scripts\xiaohongshu_cli.py --url "https://www.xiaohongshu.com/explore/<笔记ID>?xsec_token=...&xsec_source=pc_user&source=web_profile_page"
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
- **视频是中间产物，不进产物根**：视频/音频只落在项目本地的媒体暂存区 `.scratch/`，转录成功后删除。产物根只留文本产物与图片。抖音的 `--keep-video` 也只留在 `.scratch/kept/`，不写进产物根。
- 小红书视频笔记同样走「暂存 → ASR 转录 → 删除视频」，产物形态与抖音、B站一致（正文是语音文字）。
- 产物根默认是仓库内的 `10_raw/`；设 `BILIBILI_SFETCH_RAW_DIR` 可把它指到别处（跨机共享时指向同步盘，值可写未展开的 `%OneDriveConsumer%\...`，各机展开成自己的盘符）。产物根与代码仓库是两个独立的根，详见 `docs/cross-machine-sharing.md` 与 `docs/adr/0001`。
- 跨机共用同一份产物根时，**产物文件是否存在**是"已产出"的事实依据，优先于各机私有的任务库；规划器用 `is_done` 回调判定，详见 `docs/adr/0002`。
- 默认 Chrome profile 是项目根目录的 `.chrome-bilibili`；设置 `BILIBILI_SFETCH_CHROME_PROFILE` 可覆盖。
- 字幕输出为 SRT 内容，文件扩展名为 `.md`；默认路径可用 `--output` 覆盖。
- `data/videos_manifest.json` 保存来源；`data/subtitle_tasks.db` 是统一入口的任务状态主库。
- `download_list.json` 是旧入口、批处理和救援流程共用的兼容记录；CLI 会从旧 JSON 迁移，但任务规划以 SQLite 为准。
- `scripts/compile_db.json` 仅在文件存在且含对应 record 时写入字幕路径，不是任务状态库。
- ASR 需要 `faster-whisper` 和系统 `ffmpeg`；空间模式需要 `yt-dlp`。
- 抖音流程先匿名解析 `aweme/detail` 接口拿目标帖；失败才使用旧页面扫描、`yt-dlp` 和可选的 `.chrome-douyin` profile，环境变量 `DOUYIN_SFETCH_CHROME_PROFILE` 可覆盖。
- 小红书流程用「普通 Chrome + CDP」发现资源（`scripts/xiaohongshu_cdp.py`）：Playwright 自带 Chromium 带 `--enable-automation`，会被小红书风控跳登录；普通方式启动的 Chrome 不带自动化标记，headless 优先、不弹窗口。默认 profile 是 `.chrome-xiaohongshu`，环境变量 `XHS_SFETCH_CHROME_PROFILE` / `XHS_SFETCH_CDP_PROFILE` 可覆盖。签名 CDN URL 只在内存中使用。
- 小红书入口使用分享页完整 URL；无 Cookie 页面跳转 `/login` 时返回 `LOGIN_REQUIRED`，不能把登录页误判为资源不存在。
- 不提交 `.chrome-bilibili/`、`.venv/`、字幕、音频、模型缓存或账号状态。
- 不记录或提交 Cookie、Authorization header、响应 body、页面文本和原始响应 URL；不绕过 CAPTCHA、付费限制或风控。

## 关键行为

1. 来源适配器发现并去重 BV 号，写入 manifest。
2. 任务规划器根据 SQLite 状态跳过成功项，并保留失败、暂停项供重试。
3. Playwright 打开持久化 profile，监听 AI 字幕响应并写出 SRT 内容。
4. 原生字幕确认不存在且显式启用 `--asr-fallback` 时，才下载音频并本地转录。
5. 登录失败、浏览器启动失败和真实 412/429 限流会暂停任务；批量流程不把未访问视频标为失败。
6. 抖音媒体候选必须与目标 `modal_id` 关联；不使用页面中的第一个候选。
7. 抖音图文帖（`aweme_type` 68 / `media_type` 2）的 `play_addr` 指向背景音乐，不是内容本身；图文只从 `images[].download_url_list` 取图。
8. 抖音 `aweme/detail` 接口约 8 秒后才返回，等事件而非固定 sleep；小红书 `imageList` 是权威图集，DOM 兜底会漏懒加载图，页面 `N/M` 计数器可交叉核对张数。
