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
| `scripts/feishu_base.py` | `lark-cli` 封装：建表、建字段、分页读记录、批量增改、协作者授权。写入走 `@file` 临时文件，规避命令行长度限制。`list_records_v3` / `verify_field` 走 v3 列式端点并发回读校验；`list_field_options` / `ensure_field_options` 全量翻页读选项并合并后再 PUT |
| `scripts/feishu_auth.py` | agent 设备码授权：`start` 用 `--no-wait --json` 拿链接与 device code，`complete` 完成登录；权限域收敛到 `base,drive` |
| `scripts/summary_pipeline.py` | 封面摘要流水线：封面下载/转 PNG、按 `idx` 值合并摘要库、飞书写入与回读校验 |
| `scripts/xhs_board_inject.js` | 小红书 board 页内采集脚本：边滚边收 11 字段、按 note-id 去重、POST 到本地服务 |
| `scripts/xhs_board_collect.py` | 采集结果归一化/去重/落盘的服务端，以及按视频ID去重写入飞书的 `push` 入口 |
| `scripts/xhs_board_batch.py` | 按已采集的 `{id, token}` 清单批量下载**笔记正文与媒体**（不负责抓清单） |
| `scripts/sync_metadata.py` | 串起上述三步的 CLI 入口 |

## 各平台的元数据来源

### B站

| 来源 | 提供 | 说明 |
|---|---|---|
| `<产物根>/01_B站视频转录/*.md` frontmatter | title / uploader / published_at | **两种格式**：一套用 `bvid:`，另一套用 `bv:` 且带 `uid:`/`tags:` 等字段 |
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
| `<产物根>/03_小红书/*.json` | title / author / url / 点赞收藏数 | 纯数组格式 |
| `<产物根>/03_小红书/*.state.json` | 同上 | `{board_id, notes: [...]}` 包装格式 |
| board 页直采（`xhs_board_inject.js`） | note_id / token / 标题 / 作者 / 封面 / 点赞 / 是否视频 | 2026-11-18 验证，不需要打开笔记页 |
| `scripts/collect_xhs_dates.py` | published_at | 需登录，浏览器采集 |

本地两份文件合并去重后有 **838 条笔记**，标题和作者 100% 覆盖，但**所有字段里都没有发布时间**。

#### 采集路径（2026-11-18 实测修正）

**旧结论已被推翻**：`xiaohongshu_cdp.py` 里「带 `xsec_token` 免登录、空 profile 可读」的说法对 **board 页**不再成立。实测 `.chrome-xiaohongshu-clean` 空 profile 打开 board 只有「登录」按钮、0 卡片；笔记页轮询 60 秒也一直停在 `/login`。Chrome 副本 profile 同样拿不到登录态（Chrome 157 的 Cookies 独占锁，ABE 加密使副本无法解密）。

当前可行路径只有**复用用户已登录的 Chrome 扩展**（WorkBuddy 内置浏览器）。`scripts/open_xiaohongshu_login.py` 已弃用，不要运行。

#### board 页直采（不需要打开笔记详情）

board 卡片 `section.note-item` 直接给出 11 个字段：`data-note-id`、xsec_token、标题（`a.title span`）、作者名（`.author .name`）、作者 ID 与 token（`.author[href]`）、封面 `src` 与宽高、点赞数、是否视频、`data-index`。

**仅在详情页可得**：发布时间、正文、话题标签、视频时长、收藏/评论/转发数。所以「按语音/正文检索」对这批 board 采集数据天然不可用。

四个必须遵守的约束：

1. **标题上限 40 字符，平台侧截断**，完整标题可能超 80 字，写表时尾巴会丢字。
2. **board 是虚拟列表**：DOM 常驻约 32 张，滚动后旧卡片被卸载。必须边滚边收，不能滚到底再读。实测每滚 650–700px 新增约 5–6 条。
3. **`data-index` 不可信**：同一条笔记不同时刻拿到不同 index（实测 214 → 250）。**去重只能用 note-id**，用 index 算缺口会得到假缺口。
4. **后台标签页会被 Chrome 定时器节流**：`setTimeout` 被压到 1 分钟以上。`evaluate_script` 前必须 `select_page` 带 `bringToFront: true`。

实测速度：页内脚本边滚边收 202 条 / 27 秒；全量 939 条 / 约 3 分钟。数据落盘走「页面 fetch POST → 本地临时 HTTP 服务」，避开逐条 DOM 快照的 token 成本。

流程：

```powershell
# 1) 启动接收服务（另开一个终端）
python scripts\xhs_board_collect.py serve --out .scratch\xhs_board_full.json

# 2) 在已登录的 Chrome 里打开 board，注入 scripts\xhs_board_inject.js，再执行
#    await window.__xhsBoardCollector.start({endpoint:'http://127.0.0.1:8899/collect'})

# 3) 落盘后按视频ID去重写入飞书
python scripts\xhs_board_collect.py push --notes .scratch\xhs_board_full.json --base UGWbb... --table tblXXX
```

更新时间仍然只能逐条进详情页，`collect_xhs_dates.py --only <ids>` 限定要处理的 note id，避免为其他笔记浪费时间。若只需要标题/作者/封面/点赞，用 board 直采即可，不要跑逐条详情。

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
   用户能打开但**不能编辑**（`auto-grant skipped` 警告）。agent 场景用
   `python scripts\feishu_auth.py start` 拿链接与设备码，用户确认后
   `python scripts\feishu_auth.py complete <code>`，不要跑阻塞式
   `lark-cli auth login`。
5. **`lark-cli auth login` 会阻塞**。agent 场景要用 `--no-wait --json` 拿
   `verification_url` + `device_code`，生成二维码给用户扫，之后再
   `auth login --device-code <code>` 完成（已封装在 `feishu_auth.py`）。
6. **只申请需要的权限域**：`--domain base,drive`，不要用 `--recommend`/`all`
   （会拉到 100+ 权限）。`feishu_auth.py` 默认就是 `base,drive`。
7. **已有行要 update 而不是 create**，否则会重复插入。`feishu_base.upsert_records()`
   先按业务键查 `record_id` 映射再分流。
8. **表要对应"已采集清单"**。本地元数据源可能知道本项目从未采集过的视频
   （没有媒体文件、状态库里也没登记），这类不能灌进表——`sync_metadata.py`
   只同步状态库里已登记的视频。
9. **v3 列式端点 `records` GET 每页固定 20 行**，`page_size` 传 500 也被忽略；
   只能靠 `offset += len(rows)` 翻页，`page_token` 在此端点不可用。3417 行要
   171 次请求，用 `feishu_base.list_records_v3()` 并发 8 页拉取。
   响应是 `data.fields`（列名数组）+ `data.data`（行值数组）+ `data.record_id_list`，
   没有 `items` 键。
10. **select 字段选项缺失会让 batch_create 报 `800030005 not_found`**；补齐选项后
    才能自动建选项。单条 upsert 则会自建选项。补全选项用
    `feishu_base.ensure_field_options()`：它先翻页拉全量选项再合并 PUT，
    避免全量 PUT 时丢掉已有选项；载荷只传 `{"name": ...}`，带 `hue`/`lightness`
    会报 `800010701`。
11. **Git Bash 会把 `/open-apis/...` 当 Windows 绝对路径**，调 `lark-cli api`
    必须设 `MSYS_NO_PATHCONV=1`（`api_get` 已内置）。查询参数走 `--params`，
    不能拼在 path 后。

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
    playwright 会以 exitCode=21 失败。（注：`open_xiaohongshu_login.py` 已弃用；
    2026-11-18 起 board 采集走已登录的 Chrome + `xhs_board_inject.js`，不再自建 profile。）
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

写库前先备份（`sync_metadata.py --apply` 已自动执行，可用 `--no-backup` 关闭）：

```powershell
Copy-Item data\subtitle_tasks.db tmp\subtitle_tasks.db.bak.$(Get-Date -Format HHmmss)
```

默认只填空列（`title IS NULL OR title=''`），重复执行安全。
需要覆盖已有值时加 `--overwrite`。

## 摘要字段流水线

「摘要」`fldEb04MUN` 与「封面」`fldZEhWBR0` 都是 text 类型存直链/文本，
与 wiki 流程的「wiki摘要」`fldp8CZlGO` 并存。生成摘要的证据分层：

1. 有封面 → 依据封面上的明确文字线索（工具名/平台名/方法数）写一句话。
2. 无封面但标题具体 → 仅凭标题写。
3. 标题是「已失效视频」或纯 BV 号 → 跳过，无可依据内容。

webp 封面要先转 PNG 再交给视觉模型；5×5 contact sheet 用 `scripts/contact_sheet.py`
把 25 条拼成一张图，一次看图覆盖 25 条。

**最大的坑是索引错位**：manifest 的 `idx` 是显式值，不是数组位置。
`idx 205` 封面下载失败未入 manifest，用 `manifest[idx-1]` 取元数据会让其后
75 条 record_id/title 整体错位一位。`scripts/summary_pipeline.py` 的
`index_items()` / `merge_batch()` 按 `idx` 值建字典，允许缺口；永远不要用数组位置切片。

## 覆盖现状（2026-11-19 更新）

| 平台 | 记录数 | 标题 | 封面 | 摘要 |
|---|---:|---:|---:|---:|
| bilibili | 1045 | 100% | 1023 | 1029 |
| douyin | 1416 | 1367 | — | — |
| xiaohongshu | 956 | 100% | 939 | 955 |

B 站剩余 16 条是「已失效视频」或纯 BV 号（源站 62002/62012/-404），
小红书剩余 1 条是 board 自身的伪记录，三者都无可依据内容。
B 站另有 6 条封面受限但标题具体，摘要已按「仅凭标题」策略补上。

抖音没有 `download_list.json` 之类的下载时间来源。B 站标题已 100% 覆盖；
UP主 843/1045 的缺口是部分视频页面没渲染出 `owner` 字段（老视频或访问受限），
不是没抓到标题。小红书 board 直采的标题、作者、封面来自卡片本身，
发布时间/正文/话题标签仍需详情页。
