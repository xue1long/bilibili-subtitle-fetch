# SETUP.md — bilibili-subtitle-fetch 初始化配置

> 本文件补齐 README「安装」一节未覆盖的**从零初始化**步骤。
> 以下命令在 **Windows / PowerShell** 下实测可用（2026-09-22）。其他平台命令基本一致，仅 venv 激活与路径写法不同。
>
> 默认字幕抓取后端是 **Playwright**（Selenium 仅作兼容回退，`--backend selenium`）。

## 1. 环境要求

| 依赖 | 要求 | 说明 |
|------|------|------|
| Python | 3.7+（实测 3.13 可用） | 建议用虚拟环境隔离 |
| Google Chrome | 已登录 B 站账号（抖音可选） | B 站字幕需要登录态；抖音先匿名尝试 |
| ffmpeg | 系统 PATH 内 | **仅音频救援模式**需要 |
| 网络 | 可访问 bilibili.com | — |

## 2. 创建项目虚拟环境（推荐）

避免污染全局 Python：

```powershell
cd bilibili-subtitle-fetch
python -m venv .venv
# 激活（PowerShell）
.\.venv\Scripts\Activate.ps1
# 若报执行策略错误：Set-ExecutionPolicy -Scope Process RemoteSigned
```

> 也可使用 managed Python：
> `C:\Users\HP\.workbuddy\binaries\python\versions\3.13.12\python.exe -m venv .venv`

## 3. 安装 Python 依赖

仓库根目录已有 `requirements.txt`（含 `playwright` / `selenium` / `webdriver-manager` / `yt-dlp` / `faster-whisper` / `pytest`）：

```powershell
pip install --upgrade pip
pip install -r requirements.txt
```

按模式取最小依赖（等价）：

- 单视频 / 空间 / 收藏夹：`playwright selenium webdriver-manager`
- 空间模式额外：`yt-dlp`
- 音频救援额外：`faster-whisper` + 系统 `ffmpeg`

## 4. 下载 Playwright 浏览器（⚠️ 必做，README 原「安装」一节漏写）

只装 pip 包**不够**，必须下载浏览器二进制，否则 Playwright 后端直接报「浏览器未找到」：

```powershell
playwright install chromium
# 下载到 %LOCALAPPDATA%\ms-playwright（如 chromium-1243）
```

## 5. 配置 B 站登录态（⚠️ 必做）

字幕抓取需要已登录 B 站的浏览器 profile。项目约定用**独立 profile**，避免污染日常 Chrome。

### 5.1 打开登录窗口、手动登录

```powershell
# 打开真实 Chrome 窗口（headless=False），profile 落在 <项目根>/.chrome-bilibili
python scripts/open_playwright_login.py
```

- 在弹出窗口里扫码 / 账号密码登录 B 站。
- 登录态写入 `<项目根>/.chrome-bilibili`。
- ⚠️ 该脚本有 **10 分钟自动关闭**超时（`asyncio.sleep(600)`），请在此之前完成登录；超时关闭后重新运行本命令即可。

### 5.2 提取时注入同一份 profile（关键对齐点）

`subtitle_extractor.py` / `cli.py` 默认读取项目根目录的 `.chrome-bilibili` profile；也可用环境变量
`BILIBILI_SFETCH_CHROME_PROFILE` 覆盖。若使用自定义 profile，必须让登录和提取指向同一路径，否则会出现「登了也报 `LOGIN_REQUIRED`」：

```powershell
$env:BILIBILI_SFETCH_CHROME_PROFILE = "E:\002-Pr\bilibili-subtitle-fetch\.chrome-bilibili"
# 随后跑提取（见第 7 节）
```

> 其他可选路径覆盖（见 README）：`BILIBILI_SFETCH_ROOT` / `BILIBILI_SFETCH_EDGE_PROFILE` / `BILIBILI_SFETCH_CHROMEDRIVER` / `BILIBILI_SFETCH_CHROME_BIN`。

## 6. 跳过首次运行向导（⚠️ 后台 / CI 必看）

首次运行 `subtitle_extractor.py` 会触发 `_first_run_config()` 交互向导（询问保存路径并写 `config.json`），内部用 `input()`。**在后台无 stdin 的环境会直接 `EOFError` 退出**。

解决：跑提取时显式传 `--output`，即可跳过向导：

```powershell
python scripts/subtitle_extractor.py BV1vqeF62EQS --browser chrome --output "10_raw\01_B站视频转录"
```

## 7. 快速验证

```powershell
$env:BILIBILI_SFETCH_CHROME_PROFILE = "E:\002-Pr\bilibili-subtitle-fetch\.chrome-bilibili"
python scripts/subtitle_extractor.py BV1vqeF62EQS --browser chrome --output "10_raw\01_B站视频转录"
```

成功标志：`10_raw\01_B站视频转录\BV1vqeF62EQS.md` 生成，且 `download_list.json` 中该 BV 状态为 `success`。

> 实测：单视频可抓到约 190 条字幕（SRT 格式，`.md` 扩展名）。

## 8. 已知坑（来自实测）

| 现象 | 原因 | 解决 |
|------|------|------|
| Playwright 报浏览器未找到 | 只装 pip 包没下浏览器 | `playwright install chromium` |
| 报 `LOGIN_REQUIRED` | 登录和提取没有使用同一 profile | 默认使用 `.chrome-bilibili`；自定义时设 `BILIBILI_SFETCH_CHROME_PROFILE` 指向登录用的同一 profile |
| 后台运行直接退出 / `EOFError` | 首次向导 `input()` 无 stdin | 传 `--output` 跳过向导 |
| 元数据 enrichment 报 `HTTP 412` | 运行时元数据未从当前页面取到，触发 best-effort 回退请求 | 确认使用已登录 profile；即使 enrichment 失败也不影响字幕主流程 |

## 9. 统一入口（cli.py）

README 主推 `scripts/cli.py` 作为统一入口（单视频 / 收藏夹 URL / UP 空间）。本项目的登录态注入（`BILIBILI_SFETCH_CHROME_PROFILE`）与 `--output` 跳过向导对 `cli.py` 同样适用。

```powershell
python scripts\cli.py --video BVxxxxxxxxxx
python scripts\cli.py --space-url "https://space.bilibili.com/xxx/upload/video"
python scripts\cli.py --favorite-url "https://space.bilibili.com/xxx/favlist?fid=xxx"
```

## 10. 抖音匿名优先转录

抖音页面提示登录时，先直接运行单视频命令；只要目标视频已自动加载，程序会按 `modal_id` 提取视频链接，不要求先登录：

```powershell
python scripts\douyin_cli.py --url "https://www.douyin.com/user/self?modal_id=7687559858779351972&showTab=favorite_collection"
```

默认输出 `10_raw\02_抖音视频转录\DY7687559858779351972.md`，包含视频元数据和 SRT 字幕。匿名解析失败后才尝试 `yt-dlp`，最后使用项目 `.chrome-douyin`；自定义登录 profile：

```powershell
$env:DOUYIN_SFETCH_CHROME_PROFILE = "E:\002-Pr\bilibili-subtitle-fetch\.chrome-douyin"
```

抖音 ASR 同样需要 `faster-whisper` 和系统 `ffmpeg`；追加 `--keep-video` 可保留下载的视频文件。
