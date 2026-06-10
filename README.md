# bilibili-subtitle-fetch

> B 站 AI 字幕下载工具 — Claude Code Skill
>
> 单视频 / UP 主空间 / 收藏夹三种模式批量提取字幕，自动保存为 SRT 格式。
> **🆕 v1.2 下载字幕后自动抓取视频元数据（播放量 / 标题 / 简介 / 点赞量 / 上传时间）并写入字幕顶端 frontmatter。**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.7+](https://img.shields.io/badge/python-3.7+-blue.svg)](https://www.python.org/downloads/)

---

## 特性

- 🎯 **三种模式**：单视频字幕 / UP 主空间批量 / 收藏夹批量
- 🪝 **JS Hook 拦截**：与 B 站浏览器扩展相同的 XHR/Fetch Hook 技术，稳定可靠
- 📁 **自动保存到 SRT 格式**（`.md` 扩展名，与项目约定一致）
- 🆕 **v1.2 元数据自动拼入**：下载字幕后自动抓取 6 字段元数据（标题 / UP 主 / 播放量 / 点赞数 / 简介 / 上传时间）作为 YAML frontmatter 写到字幕文件顶端
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

### 🆕 v1.2 额外依赖

v1.2 新增的"自动拼元数据 frontmatter"功能需要 **[`bilibili-video-meta`](https://github.com/xue1long/bilibili-video-meta)** 作为兄弟 skill。请把 `bilibili-video-meta` 安装到 `../bilibili-video-meta/` 相对位置（与本 skill 同级的 `skills/` 目录下），或在调用时使用 `--no-meta` 关闭此功能。

目录结构示例：

```
.claude/skills/
├── bilibili-subtitle-fetch/    # 本 skill
└── bilibili-video-meta/         # 必需（v1.2 起）
```

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

### 🆕 v1.2+ 输出示例

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

数据来自兄弟 skill [`bilibili-video-meta`](https://github.com/xue1long/bilibili-video-meta)，无需登录、抓取稳定。

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

- [`bilibili-video-meta`](https://github.com/xue1long/bilibili-video-meta) — 视频元数据抓取（本 skill v1.2+ 的依赖）
- [video-wiki-compile](https://github.com/) — 视频笔记 Wiki 编译流水线

---

## License

MIT — 详见 [LICENSE](LICENSE) 文件。

---

## 致谢

- B 站浏览器扩展的 XHR/Fetch Hook 思路
- [`bilibili-video-meta`](https://github.com/xue1long/bilibili-video-meta) 的元数据抓取能力
