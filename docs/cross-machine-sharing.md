# 跨机共享产物根

让多台电脑共用同一份下载产物。设计依据见 `docs/adr/0001-artifact-root-decoupled-from-repo.md`
与 `docs/adr/0002-artifact-existence-as-dedupe-truth.md`。

## 原理

```
机器 A（本机）                    OneDrive                机器 B
代码仓库（本地 git clone）  ←→   产物根（唯一一份）  ←→   代码仓库（同一份 git）
  .venv / cookie / 任务库留本机     平台编号目录            .venv / cookie / 任务库留本机
```

- **代码树留本地**：各机 clone 同一份 git。`.venv`（475 MB）、`.chrome-*`（含登录 cookie）、
  `data/*.db`（SQLite）天然不出本机，不需要任何排除规则。
- **产物树唯一一份**：放在同步盘，两机写同一个目录。
- **去重靠产物本身**：规划器以"产物文件是否存在"作为已产出的事实依据，不依赖各机私有的任务库。

## 配置：产物根指向同步盘

设置**用户级**环境变量 `BILIBILI_SFETCH_RAW_DIR`，值写成**未展开**的引用：

```
BILIBILI_SFETCH_RAW_DIR=%OneDriveConsumer%\009-视频笔记
```

未展开是关键：`%OneDriveConsumer%` 由每台机器自己展开成自己的同步盘根（本机是 `D:\OneDrive`，
另一台可能装在 C 盘），**同一条配置字符串在所有机器上都成立**。代码在读取时用
`os.path.expandvars` 展开；引用的变量不存在时会直接报错，不会静默写到错误位置。

设置方式（PowerShell，注意 `setx` 也会原样存储 `%VAR%`，但用 .NET 更可控）：

```powershell
$pct = [char]37
[Environment]::SetEnvironmentVariable('BILIBILI_SFETCH_RAW_DIR', $pct + 'OneDriveConsumer' + $pct + '\009-视频笔记', 'User')
```

不设该变量时，产物仍落在仓库内的 `10_raw/`，单机用法完全不受影响。

## 另一台机器的落地清单

1. `git clone https://github.com/xue1long/bilibili-subtitle-fetch.git`
2. 建虚拟环境并装依赖：
   ```powershell
   python -m venv .venv
   .\.venv\Scripts\python.exe -m pip install -r requirements.txt
   .\.venv\Scripts\python.exe -m playwright install chromium
   ```
3. 设**同名**环境变量 `BILIBILI_SFETCH_RAW_DIR=%OneDriveConsumer%\009-视频笔记`
4. OneDrive 登录同一账户；把产物根设为「始终保留在此设备上」：
   ```powershell
   attrib +P "%OneDriveConsumer%\009-视频笔记" /S /D
   ```
   （若个别文件没带上 P 属性，单独再打一次；`.NET` 的 `Attributes` 枚举读不到 P 位，
   请用 `attrib <路径>` 查看原始标记。）
5. 各平台 Chrome profile **各自登录**（cookie 不跨机，这是有意的）：
   `python scripts/open_playwright_login.py`、`python scripts/open_xiaohongshu_login.py`

## 去重怎么生效

`scripts/cli.py` 在规划任务时同时看两处：

- 本机任务库 `data/subtitle_tasks.db`（各机私有，保留失败/暂停等中间态）
- 产物文件是否已存在（`01_B站视频转录/{BV}.md`）—— 跨机共享的真正依据

产物已存在即跳过，与任务库无关。两机写同一个产物根，天然互斥。

## 已知限制

1. **「无字幕」视频没有产物文件**，另一台机器会重跑一次并得出相同结论——只浪费一次抓取，
   不产生错误产物。这类"确认无产物"的状态只记在本机任务库里。
2. **产物写入到同步盘完成之间有窗口期**：此刻另一台机器规划任务，仍可能重复下载一次。
3. **`subtitle_extractor.py` 会记住上次的输出目录**（存在 `config.json` 的 `output_dir`）。
   迁移产物后若该文件残留旧路径，会顽固使用旧目录，需清一次。
4. **同步盘上的文件在另一台机器上可能是云端占位符**，读取会触发按需下载；
   把产物根设为常驻可避免。

## 排障

| 现象 | 原因与处理 |
|---|---|
| 产物还是落在仓库里 | 环境变量没生效：新开的终端里 `echo %BILIBILI_SFETCH_RAW_DIR%` 看是否有值；变量在注册表 `HKCU:\Environment` 里时，已启动的进程读不到 |
| 报错 `BILIBILI_SFETCH_RAW_DIR 未展开` | 引用的环境变量（如 `OneDriveConsumer`）在该机不存在——OneDrive 客户端没登录过，或用的是另一个账户 |
| 另一台机器看不到产物 | 同步盘里确认该账户下有 `009-视频笔记`；产物根是否被设成了"仅此设备" |
| 另一台机器重复下载了 | 同步延迟窗口，或该内容本来就没有产物文件（无字幕） |
