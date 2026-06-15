---
name: bilibili-subtitle-fetch
description: B站AI字幕下载工具，支持单视频、UP主空间、收藏夹三种模式提取字幕。使用JS Hook拦截技术（与B站浏览器扩展相同），自动保存为SRT文件。**🆕 v1.4 下载列表去重**：download_list.json 跟踪下载状态，成功/失败全程记录。**🆕 v1.3 仓库自带 extract_meta.py（vendor 自 bilibili-video-meta），零外部依赖即可自动抓取视频元数据（播放量/标题/简介/点赞量/上传时间）并写入字幕顶端 frontmatter。** 当用户说"下载字幕"、"提取字幕"、"导出字幕"、"AI字幕"、"BV号字幕"时触发。收藏夹模式需先运行 update-bilibili-favorites 更新视频列表。
context: fork
agent: general-purpose
---

# Bilibili AI Subtitle Fetch

B站AI字幕下载工具，支持三种模式：
1. **单视频字幕** — 输入BV号或视频URL
2. **UP主空间字幕** — 输入UP主上传视频页面URL，批量下载
3. **收藏夹字幕** — 读取已有 `videos_fav.json`，批量下载收藏夹视频字幕

## 工作流程

```dot
digraph {
  rankdir=TB;
  user[label="用户需求", shape=oval];
  skill[label="bilibili-subtitle-fetch", shape=box"];
  single[label="单视频字幕", shape=box];
  space[label="UP主空间字幕", shape=box];
  fav[label="收藏夹字幕", shape=box];
  hook[label="JS Hook拦截\n(同浏览器扩展)", shape=box];
  srt[label=".srt 字幕文件", shape=cylinder];
  meta[label="🆕 v1.2 自动拼元数据", shape=box, style=filled, fillcolor=lightyellow];
  metaFetch[label="调用 bilibili-video-meta\n抓 6 字段", shape=box];
  frontmatter[label="prepend_meta.py\n拼 YAML frontmatter", shape=box];

  user -> skill;
  skill -> single -> hook -> srt -> meta;
  skill -> space -> hook -> srt -> meta;
  skill -> fav -> hook -> srt -> meta;
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

## 前置条件

- Microsoft Edge 浏览器已登录 B站账号
- Python 3 + selenium 包
- 已运行 `update-bilibili-favorites`（收藏夹模式必需）

## 使用方式

### 方式一：命令行

```bash
# 单视频字幕
python .claude/skills/bilibili-subtitle-fetch/scripts/subtitle_extractor.py BV11RffBdEEQ

# UP主空间字幕
python .claude/skills/bilibili-subtitle-fetch/scripts/subtitle_extractor.py --space "https://space.bilibili.com/3546663834618256/upload/video"

# 收藏夹字幕
python .claude/skills/bilibili-subtitle-fetch/scripts/subtitle_extractor.py --favorites
```

### 方式二：通过数据库（批量处理）

```bash
# 查看待下载字幕的视频（videos.db 是 karpathy 上游项目的下载流水线 DB，本项目不需要）
python -c "import sqlite3; c=sqlite3.connect('database/videos.db'); print(c.execute('SELECT status,COUNT(*) FROM videos GROUP BY status').fetchall())"

# 下载 pending 视频的字幕
python .claude/skills/bilibili-subtitle-fetch/scripts/download_subtitles_from_db.py
```

> **v1.1 变更**：方式二依赖 karpathy 的 `videos.db`，**本项目里不可用**。本项目走 wiki 流水线：脚本会自动按 `compile_db.json` 去重，新视频下载后会在 wiki DB 记录里增 `subtitle_srt_path` 字段。

## 输出格式

- 文件名：`{BV号}.md`（SRT 时间轴格式存为 .md）
- 位置：`000_Raw/01_B站视频转录/`（与现有 .txt/.md 转录稿同目录；可通过 `--output` 覆盖）
- 格式：
  - **v1.2+ 字幕顶端带 YAML frontmatter**（自动抓取的 6 字段元数据，详见下文）
  - SRT 时间轴 + 中文/英文/双语字幕内容（内容格式不变，仅扩展名改为 .md）

## 🆕 v1.2 元数据自动拼入

下载字幕成功后，**自动**调用 [[.claude/skills/bilibili-video-meta/SKILL|bilibili-video-meta]] 抓取视频原数据，拼成 YAML frontmatter 写到字幕文件顶端。无需二次调用。

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
python .claude/skills/bilibili-subtitle-fetch/scripts/subtitle_extractor.py BV1xxx --no-meta
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
      "subtitle_path": "10_Raw/01_B站视频转录/BV11RffBdEEQ.md"
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

**日志示例**：
```
[下载列表] 已加载 5 条记录: download_list.json
[跳过] BV11RffBdEEQ - 下载列表中已存在
```

## 核心脚本

| 脚本 | 说明 |
|------|------|
| `subtitle_extractor.py` | 主脚本，支持三种模式（单视频/UP主空间/收藏夹），保存后自动调 prepend_meta.py |
| `prepend_meta.py` | 🆕 v1.2 把 bilibili-video-meta 的 JSON 输出拼成 YAML frontmatter 写到字幕顶端 |

## 已知问题

1. **第二次必成功**：首次失败后重试几乎必成功（Edge session问题）
2. **需要登录态**：AI字幕需B站账号登录，未登录只能获取普通字幕
3. **单字幕优先**：多字幕时默认选第一个语言项
4. **Windows Unicode**：打印Unicode符号可能报错，脚本已处理

## 依赖安装

```bash
pip install selenium webdriver-manager
```

## 流程说明

### 单视频字幕流程

1. 关闭 Edge 实例（避免 session 冲突）
2. 启动 Edge（加载用户 profile）
3. 注入 JS Hook（拦截 `aisubtitle.hdslb.com` 请求）
4. 打开视频页面，等待播放器加载
5. 点击字幕按钮，选择第一个语言项
6. 拦截响应数据，解析 body 字段
7. 转换为 SRT 格式，保存文件
8. 关闭浏览器

### UP主空间字幕流程

1. 通过 yt-dlp 提取该空间下所有视频 BV 号
2. 循环调用单视频字幕流程
3. 增量保存（已下载的字幕跳过）

### 收藏夹字幕流程

1. 读取 `videos_fav.json`
2. 循环调用单视频字幕流程
3. 增量保存（已下载的字幕跳过）

## 注意事项

- 字幕下载需要 B站登录态，Cookie 过期会导致失败
- 批量下载建议加延时，避免请求过快被限频
- 字幕路径会写入 `compile_db.json` 的 `subtitle_srt_path` 字段，便于后续 wiki compile 流程读取
- 如果视频没有 AI 字幕，脚本会提示但不会报错