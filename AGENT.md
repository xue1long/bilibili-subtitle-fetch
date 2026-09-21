# Agent Guide

## 项目目标

这是一个 B 站字幕抓取工具。它支持单视频、UP 主空间、收藏夹、搜索批量和音频 ASR 救援，并将字幕保存为 SRT 时间轴格式的 `.md` 文件。

## 目录结构

- `scripts/subtitle_extractor.py`：主入口，支持单视频、空间和收藏夹模式。
- `scripts/bili_guard.py`：登录状态、字幕请求探针、限流和熔断逻辑。
- `scripts/runtime_paths.py`：项目根目录、输出目录和浏览器 profile 路径解析。
- `scripts/fetch_search_bvids.py`：使用 Playwright 抓取搜索结果 BV 号。
- `scripts/run_subtitle_batch.py`：批量调用字幕提取器。
- `scripts/rescue_one_subtitle.py`：下载音频并使用 faster-whisper 转录。
- `scripts/subtitle_rescue.py`：可复用的音频下载、ASR 转录和救援函数。
- `scripts/cli.py`：统一单视频、收藏夹 URL、UP 主空间入口。
- `scripts/sources/`：视频来源适配器，统一输出 BV 视频实体。
- `scripts/pipeline/`：统一任务模型和去重规划器。
- `scripts/backends/`：Playwright 原生字幕和 ASR 后端。
- `scripts/storage/`：来源 manifest、JSON 兼容状态和 SQLite 状态库。
- `scripts/extract_meta.py`：提取视频元数据。
- `scripts/prepend_meta.py`：将元数据写入字幕文件 frontmatter。
- `tests/`：单元测试和 CLI/浏览器生命周期测试。
- `10_raw/01_B站视频转录/`：默认字幕输出目录。

## 主要运行方式

使用项目虚拟环境：

```powershell
.\.venv\Scripts\Activate.ps1
```

单视频字幕：

```powershell
python scripts\subtitle_extractor.py BVxxxxxxxxxx --browser chrome
```

统一入口：

```powershell
python scripts\cli.py --video BVxxxxxxxxxx
python scripts\cli.py --favorite-url "https://space.bilibili.com/.../favlist?fid=..."
python scripts\cli.py --space-url "https://space.bilibili.com/.../upload/video"
```

主流程默认使用 Playwright。项目登录态使用独立 profile：

```powershell
$env:BILIBILI_SFETCH_CHROME_PROFILE = "D:\000\bilibili-subtitle-fetch\.chrome-bilibili"
```

原生字幕不存在时显式启用 ASR 救援：

```powershell
python scripts\subtitle_extractor.py BVxxxxxxxxxx --asr-fallback --asr-model small
```

ASR 默认关闭；登录、限流和浏览器启动失败不会触发救援。成功记录会标记 `source=asr` 和模型名。

统一 CLI 的核心状态库为 `data\subtitle_tasks.db`，来源清单为
`data\videos_manifest.json`。`download_list.json` 和 `subtitle_jobs.json`
仅用于兼容旧流程和首次迁移。

只有兼容旧流程或专门测试 Selenium 时才使用：

```powershell
python scripts\subtitle_extractor.py BVxxxxxxxxxx --backend selenium
```

音频救援：

```powershell
python scripts\rescue_one_subtitle.py BVxxxxxxxxxx --model small
```

音频救援需要 `ffmpeg` 和 `faster-whisper`。

## 核心流程

1. 检查 `download_list.json`，成功记录直接跳过。
2. Playwright 使用持久化 profile 打开 B 站视频页。
3. 点击播放器字幕按钮并选择第一个字幕语言。
4. 监听 `aisubtitle.hdslb.com` 或 `ai_subtitle` 响应。
5. 解析响应中的 `body`，将 `from`、`to`、`content` 转为 SRT 时间轴。
6. 写入 `{output_dir}/{BV号}.md`，更新下载列表和项目数据库。
7. 元数据增强失败不能阻断字幕主流程。

字幕抓取需要有效的 B 站登录态。不要从日志、测试输出或提交内容中暴露 Cookie、Authorization header、响应 body 或个人浏览器数据。

## 开发约定

- 优先修改现有模块和公共辅助函数，避免复制整套抓取流程。
- 默认后端保持 Playwright；不要把 Selenium 或 ChromeDriver 重新设为默认。
- 使用 `BILIBILI_SFETCH_CHROME_PROFILE` 配置项目专用 profile，避免复用用户正在使用的 Chrome profile。
- 处理字幕响应时只接受包含非空 `body` 列表的有效 payload。
- 保持下载记录的成功、失败、限流和登录失败状态语义不变。
- 不绕过 CAPTCHA、付费限制、风控或浏览器安全提示。
- 不提交 `.chrome-bilibili/`、`.venv/`、下载字幕、音频、模型缓存或包含账号状态的文件。

## 测试与验证

运行完整测试：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

修改字幕响应解析、浏览器后端或下载状态逻辑时，至少补充对应测试，并用一个实际 BV 号做一次端到端验证。完成前必须确认测试输出中的失败数为 0。

## 常见问题

- `LOGIN_REQUIRED`：检查项目专用 Playwright profile 是否已登录 B 站。
- `未找到字幕按钮`：通常是页面未加载完成、登录失效或触发风控。
- `未捕获到 AI 字幕响应`：视频可能没有可用 AI 字幕，或字幕接口被限流。
- 需要 Selenium 回退时，设置 `--backend selenium`，并确保 ChromeDriver 与实际浏览器主版本匹配。
- 批量抓取必须保留默认延迟和熔断机制，不要为了速度移除限流保护。
