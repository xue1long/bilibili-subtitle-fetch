# bilibili-subtitle-fetch

> B 站 AI 字幕下载工具 — Claude Code Skill
>
> 单视频 / UP 主空间 / 收藏夹三种模式批量提取字幕，自动保存为 SRT 格式。
> **🆕 v1.3 仓库自带 `extract_meta.py`（vendor 自 bilibili-video-meta），零外部依赖即可自动抓取视频元数据（播放量 / 标题 / 简介 / 点赞量 / 上传时间）并写入字幕顶端 frontmatter。**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.7+](https://img.shields.io/badge/python-3.7+-blue.svg)](https://www.python.org/downloads/)

---

## 特性

- 🎯 **三种模式**：单视频字幕 / UP 主空间批量 / 收藏夹批量
- 🪝 **JS Hook 拦截**：与 B 站浏览器扩展相同的 XHR/Fetch Hook 技术，稳定可靠
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
                                       JS Hook 拦截 (Edge)
                                              ↓
                                       .srt 字幕文件
                                              ↓
                          🆕 v1.2: 自动调 bilibili-video-meta
                                              ↓
                                       YAML frontmatter
```

---

## 安装

### 前置条件

- Python 3.7+
- Microsoft Edge 浏览器（已登录 B 站账号）
- `selenium` 和 `webdriver-manager`：

```bash
pip install selenium webdriver-manager
```

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

> 收藏夹模式需先运行 `update-bilibili-favorites` 生成 `videos_fav.json`。

### 🆕 v1.2 元数据开关

```bash
# 关闭元数据 frontmatter（保留纯净 SRT）
python scripts/subtitle_extractor.py BV1xxxxxxxxxx --no-meta
```

### 自定义输出目录

```bash
python scripts/subtitle_extractor.py BV1xxxxxxxxxx --output /path/to/output/
```

---

## 输出格式

文件保存到 `00_Raw/01_B站视频转录/{BV号}.md`（默认路径，可用 `--output` 覆盖）：

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

元数据来自 bundled 的 `extract_meta.py`（vendor 自 [`bilibili-video-meta`](https://github.com/xue1long/bilibili-video-meta) v1.2），无需登录、抓取稳定。

---

## 跳过逻辑（去重）

每次提取前两层去重检查，命中任意一层即跳过：

| 层级 | 数据源 | 命中条件 |
|------|--------|----------|
| 1 | 磁盘 | `00_Raw/01_B站视频转录/{BV号}.md` 已存在 |
| 2 | Wiki DB | `scripts/compile_db.json` 的 `records[].id` 已包含此 BV 号 |

---

## 已知问题

1. **第二次必成功**：首次失败后重试几乎必成功（Edge session 问题）
2. **需要登录态**：AI 字幕需 B 站账号登录，未登录只能获取普通字幕
3. **单字幕优先**：多字幕时默认选第一个语言项
4. **Windows Unicode**：打印 Unicode 符号可能报错，脚本已处理

---

## 依赖安装汇总

```bash
pip install selenium webdriver-manager
# Python 标准库：json, re, time, urllib, gzip, zlib（无需安装）
```

---

## 相关项目

- [`bilibili-video-meta`](https://github.com/xue1long/bilibili-video-meta) — 视频元数据独立 skill（v1.3 起核心代码已 vendor 进本仓库；如需独立抓元数据，可单独安装）
- [video-wiki-compile](https://github.com/) — 视频笔记 Wiki 编译流水线

---

## 文件结构

```
bilibili-subtitle-fetch/
├── SKILL.md                       # Claude Code skill 描述
├── README.md                      # 本文件
├── LICENSE                        # MIT
├── .gitignore
└── scripts/
    ├── subtitle_extractor.py      # 主脚本（Selenium + JS Hook 抓字幕）
    ├── extract_meta.py            # 🆕 v1.3 vendor：抓视频元数据（自 bilibili-video-meta v1.2）
    └── prepend_meta.py            # 🆕 v1.2：把元数据拼成 frontmatter 写到字幕顶端
```

---

## 版本历史

- **v1.3** (2026-06-10) — Vendor `extract_meta.py`，零外部依赖
- **v1.2** (2026-06-10) — 新增"下载字幕后自动拼元数据 frontmatter"功能
- **v1.1** — 移除对 karpathy `videos.db` 的依赖，改用项目自有 `compile_db.json`
- **v1.0** — 初始发布：单视频 / UP 主空间 / 收藏夹三种模式

---

## License

MIT — 详见 [LICENSE](LICENSE) 文件。

---

## 致谢

- B 站浏览器扩展的 XHR/Fetch Hook 思路
- [`bilibili-video-meta`](https://github.com/xue1long/bilibili-video-meta) 的元数据抓取能力（v1.3 起核心代码 vendor 自该项目）
