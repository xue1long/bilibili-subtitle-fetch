# 元数据流水线

把 B站/抖音/小红书视频的元数据（标题、UP主、发布时间、下载时间）收集起来，
写入状态库，并同步到飞书多维表格。

```
本地源 ──┐
          ├─→ 合并 ─→ SQLite + manifest ─→ 飞书 Base
浏览器 ──┘
```

## 快速开始

```powershell
# 1. 看本地源能覆盖多少（不写任何数据）
python scripts\sync_metadata.py --report

# 2. 写入状态库与 manifest（只填空列，可重复执行）
python scripts\sync_metadata.py --apply

# 3. 同步到飞书多维表格
python scripts\sync_metadata.py --apply --feishu-token UGWbb... --feishu-table tblXXX
```

缺字段时用浏览器补：

```powershell
# 生成待抓 ID 列表
python -c "import json;print('\n'.join(json.load(open('tmp/bili_gap.json'))))" > tmp/gap.txt

# 抓取（结果落 JSONL 缓存，失败项可重跑）
python scripts\platform_meta_browser.py --platform bilibili --ids tmp\gap.txt --out tmp\bili_meta.jsonl
```

## 模块

| 文件 | 职责 |
|---|---|
| `scripts/platform_meta.py` | 读取各平台本地源，归一化成 `{video_id: {title, uploader, published_at, ...}}` |
| `scripts/platform_meta_browser.py` | 浏览器兜底抓取（抖音/B站），JSONL 缓存可续跑 |
| `scripts/feishu_base.py` | `lark-cli` 封装：建表、建字段、分页读记录、批量增改、协作者授权。写入走 `@file` 临时文件，规避命令行长度限制 |
| `scripts/sync_metadata.py` | 串起上述三步的 CLI 入口 |

## 各平台的元数据来源

### B站

| 来源 | 提供 | 说明 |
|---|---|---|
| `10_raw/01_B站视频转录/*.md` frontmatter | title / uploader / published_at | **两种格式**：一套用 `bvid:`，另一套用 `bv:` 且带 `uid:`/`tags:` 等字段 |
| `data/videos_manifest.json` | title | 660 条，部分视频只有 manifest 有标题 |
| `download_list.json` | downloaded_at | 1045 条全有，是唯一 100% 覆盖的字段 |

frontmatter 的 `video_published_at` 两种格式的引号风格不同（`2026-01-02` vs `'2026-02-13'`），
解析时要剥引号。

### 抖音

本地库（"抖珍藏"导出）位于 `D:\admin\下载\Bili\抖音视频\data\.appdata\`，
可用环境变量 `DOUZHENCANG_APPDATA` 覆盖。三个 `db_*.js` 文件的格式都是：

```js
window.dbv_base64="<gzip + base64 的 JSON>"
```

- `db_videos.js` → `authorId` / `createTime` / `diggCount`
- `db_authors.js` → `nicknames` 数组，**取最后一个**（最新昵称）
- `db_texts.js` → 视频文案，**首行即标题**

`createTime` 是 Unix 秒级时间戳，转成本地时区的 `YYYY-MM-DD`。

### 小红书

| 来源 | 提供 | 说明 |
|---|---|---|
| `10_raw/03_小红书收藏夹/*.json` | title / author / url / 点赞收藏数 | 纯数组格式 |
| `10_raw/03_小红书收藏夹/*.state.json` | 同上 | `{board_id, notes: [...]}` 包装格式 |
| `scripts/collect_xhs_dates.py` | published_at | 需登录，浏览器采集 |

本地两份文件合并去重后有 **838 条笔记**，标题和作者 100% 覆盖，但**所有字段里都没有发布时间**。

**小红书必须登录**，与抖音/B站根本不同：

- 裸 URL `https://www.xiaohongshu.com/explore/<id>` → 跳 `/login`
- **连首页** → 也跳 `/login`
- 带 `xsec_token` 的分享链接 → 跳「安全限制」（token 数周即失效）
- 纯 HTTP 请求返回 96KB 壳页面，无任何元数据

所以要拿发布时间必须用登录后的持久化 profile：

```powershell
# 1. 一次性登录（弹出 Edge 窗口，扫码后脚本自动检测并保存 profile）
python scripts\open_xiaohongshu_login.py

# 2. 采集发布时间（只处理状态库里已有的笔记）
python scripts\collect_xhs_dates.py --board 6940b400000000001103e85d --only tmp\xhs_wanted.txt
```

采集脚本先从收藏夹页**滚动收集新 token**（旧 token 已失效），再逐条访问笔记详情。
`--only` 限定要处理的 note id，避免为收藏夹里其他笔记浪费时间。

**发布时间在 `.date` 元素里**，有三种形态，都由 `parse_xhs_date()` 解析：

| 形态 | 例子 | 处理 |
|---|---|---|
| 相对时间 | `5小时前` | 按当前时间回推 |
| 月-日 | `10-09` | 年份需推断：比今天晚超 31 天则属于上一年 |
| 完整日期 | `2024-10-09` | 直接用 |

元素文本还带**前缀和省份后缀**（`编辑于 10-15 浙江`），解析时都要剥掉。
漏掉前缀会让 60% 的日期解析失败——这个坑很隐蔽，因为相对时间和月-日格式都能解析成功，
只有带「编辑于」的会静默返回空。

小红书笔记详情是弹层渲染，`innerText` 抓不到，必须用 `textContent`。

## 踩坑记录

### 飞书 CLI

1. **`--json` 支持 `@file`**，且必须用：
   记录写入走 `@/tmp/xxx.json` 时，长标题会直接撑爆 Windows 命令行
   （约 8k–32k 字符，报「命令行太长」）。`feishu_base.py` 已统一走临时文件。
2. **`record-list` 单次最多 2000 条**。表更大时要用 `--offset` 翻页，
   `feishu_base.list_records()` 已封装。清理多余记录时同理——
   只读第一页会漏掉后面的行。
3. **`update_records` 必须是 `{record_id: fields}` 映射**，不能是
   `[[id, fields], ...]` 数组对，否则报 `800010701 invalid_request`。
4. **建表前必须先 `auth login`**，否则表里只有 bot 一个协作者，
   用户能打开但**不能编辑**（`auto-grant skipped` 警告）。
5. **`lark-cli auth login` 会阻塞**。agent 场景要用 `--no-wait --json` 拿
   `verification_url` + `device_code`，生成二维码给用户扫，之后再
   `auth login --device-code <code>` 完成。
6. **只申请需要的权限域**：`--domain base,drive`，不要用 `--recommend`/`all`
   （会拉到 100+ 权限）。
7. **已有行要 update 而不是 create**，否则会重复插入。`feishu_base.upsert_records()`
   先按业务键查 `record_id` 映射再分流。
8. **表要对应"已采集清单"**。本地元数据源可能知道本项目从未采集过的视频
   （没有媒体文件、状态库里也没登记），这类不能灌进表——`sync_metadata.py`
   只同步状态库里已登记的视频。

### 平台抓取

7. **抖音对纯 HTTP 返回 JS 加壳页**（`_$jsvmprt`），HTML 里没有任何元数据。
   必须浏览器渲染。
8. **抖音 SSR meta 注入很晚**，等待不足 10s 拿不到 `<meta name="description">`。
   代码里用 11s + 2 次重试。
9. **抖音文案含裸换行**，`[^"]*` 这类正则会在换行处截断。改用宽松匹配
   `<meta[^>]+name="description"[^>]*content="(.*?)"\s*/?>` 加实体解码、空白归一。
10. **B站的 `__INITIAL_STATE__` 是 IIFE 包裹的**：

    ```js
    window.__INITIAL_STATE__={};(function(){var s=window.__INITIAL_STATE__;s.videoData={...}})();
    ```

    正则永远匹配不到 `videoData`。正确做法是 `page.evaluate` 在页面内取值：

    ```python
    await page.evaluate("() => window.__INITIAL_STATE__.videoData")
    ```

11. **B站 `.pubdate` 类不存在**，发布时间只在 `videoData.pubdate`（Unix 秒）里。
12. **小红书笔记详情是弹层**，`innerText` 返回空，要用 `textContent`。
13. **小红书旧 `xsec_token` 会失效**，需要从收藏夹页滚动重新收集。
14. **持久化 profile 不能被两个浏览器同时占用**——登录窗口没关就启动采集，
    playwright 会以 exitCode=21 失败。先关掉登录窗口。
15. **强杀采集进程会丢登录态**。`TaskStop` 杀掉浏览器时 profile 里的
    `Default/Cookies` 还没落盘，下次就是未登录状态。登录后要**手动关闭浏览器
    窗口**，让 profile 正常保存。
16. **收藏夹页要滚动才能拿到全部笔记**，且**笔记按时间倒序**——状态库里较早
    采集的笔记排在后面。只采前几十条会大量落空。
    `collect_xhs_dates.py --only <ids>` 可以只处理需要的那些，
    但仍要滚够轮数（`--rounds`）才能让它们出现在链接列表里。

### 环境与脚本

12. **`python -c` 里带 `^`/`$` 的正则会被 bash 吞掉**，静默返回 0 匹配且不报错。
    含正则的探测逻辑必须写成 `.py` 文件再执行。
13. **`glob.glob()` 在中文目录上匹配异常**（静默漏文件），用 `os.listdir` 更可靠。
14. **本机没有 Chrome**，只有 Edge。`platform_meta_browser.find_browser()`
    按优先级找 Chrome/Edge，找不到才用 playwright 自带的 Chromium。
15. **playwright 只装在系统 Python 3.11**（`C:/Program Files/Python311/python.exe`），
    managed 3.13 环境里没有，且 GFW 下 pip 装不上。
16. **B站 frontmatter 有两套字段名**（`bvid:` / `bv:`），只认一种会漏掉 100+ 条。

## 一致性约定

元数据在三处保持一致，任何一处更新都要同步另外两处：

1. `data/subtitle_tasks.db` 的 `videos` 表（主库）
2. `data/platform_videos_manifest.json`（抖音/小红书）与 `data/videos_manifest.json`（B站）
3. 飞书多维表格

写库前先备份：

```powershell
Copy-Item data\subtitle_tasks.db tmp\subtitle_tasks.db.bak.$(Get-Date -Format HHmmss)
```

默认只填空列（`title IS NULL OR title=''`），重复执行安全。
需要覆盖已有值时加 `--overwrite`。

## 覆盖现状（2026-10-24）

| 平台 | 记录数 | 标题 | UP主 | 上传时间 | 下载时间 |
|---|---:|---:|---:|---:|---:|
| bilibili | 1045 | 1045 | 843 | 843 | 1045 |
| douyin | 1416 | 1367 | 1378 | 1378 | 0 |
| xiaohongshu | 369 | 0 | 0 | 0 | 0 |

抖音和小红书没有 `download_list.json` 之类的下载时间来源。

B站标题已 100% 覆盖。UP主 843/1045 的缺口是部分视频页面没渲染出 `owner` 字段
（老视频或访问受限），不是没抓到标题。小红书完全空白——本地无源，只能逐条抓。
