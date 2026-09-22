#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bilibili-subtitle-fetch / prepend_meta.py

把视频元数据（来自 bilibili-video-meta）作为 YAML frontmatter 拼到字幕文件顶端。

输入：
  - JSON 字符串（来自 extract_meta.py 的 stdout），通过 stdin 或 --meta-file
  - 字幕文件路径，通过位置参数

输出：
  - 在原字幕文件顶端插入 frontmatter（原内容保留，字段缺失跳过）
  - 若文件已有 frontmatter（以 `---` 开头），先剥离再插入（幂等）
  - 失败时返回非零退出码 + stderr 说明，不破坏原文件

字段映射（meta → frontmatter）：
  - title              → title
  - uploader           → uploader
  - view_count         → view_count
  - like_count         → like_count
  - description        → description (| 块，保留换行)
  - video_published_at → video_published_at

设计原则：
  1. 幂等 — 重复执行不会重复加 frontmatter
  2. 字段缺失容忍 — view_count 抓不到时只写其他字段，不报错
  3. 原子写 — 写失败时原文件保持原样
"""
import argparse
import io
import json
import re
import sys
from pathlib import Path
from typing import Optional

# Windows GBK 控制台兼容
if sys.platform == "win32" and __name__ == "__main__":
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    except Exception:
        pass

# Frontmatter 起始/结束标记
FM_START_RE = re.compile(r"^---\s*\n", re.MULTILINE)
# 已存在 frontmatter 的整块（从首个 --- 行到下一个 --- 行）
# 使用 re.MULTILINE 让 ^ 匹配行首；DOTALL 让 . 跨行匹配到下一个 ---
EXISTING_FM_RE = re.compile(r"^---\s*\n.*?\n---\s*\n", re.MULTILINE | re.DOTALL)
# BOM 字符（U+FEFF），可能在文件开头出现
BOM = "﻿"


def build_frontmatter(meta: dict) -> str:
    """从 meta dict 构建 YAML frontmatter 文本

    字段顺序固定（与 wiki 卡片 frontmatter 模板对齐）：
      title → uploader → view_count → like_count →
      video_published_at → bvid → url → description
    """
    lines = ["---"]

    if meta.get("platform"):
        lines.append(f"platform: {_yaml_escape(meta['platform'])}")
    if meta.get("video_id"):
        lines.append(f"video_id: {_yaml_escape(str(meta['video_id']))}")
    if meta.get("note_id"):
        lines.append(f"note_id: {_yaml_escape(str(meta['note_id']))}")
    if meta.get("note_type"):
        lines.append(f"note_type: {_yaml_escape(meta['note_type'])}")

    # 基础字段（出现顺序敏感：与 wiki 卡片模板一致）
    if meta.get("title"):
        lines.append(f"title: {_yaml_escape(meta['title'])}")
    if meta.get("uploader"):
        lines.append(f"uploader: {_yaml_escape(meta['uploader'])}")
    if meta.get("view_count") is not None:
        lines.append(f"view_count: {meta['view_count']}")
    if meta.get("like_count") is not None:
        lines.append(f"like_count: {meta['like_count']}")
    if meta.get("video_published_at"):
        lines.append(f"video_published_at: {meta['video_published_at']}")

    # 关联字段
    if meta.get("bv"):
        lines.append(f"bvid: {meta['bv']}")
    if meta.get("bv"):
        lines.append(f"url: https://www.bilibili.com/video/{meta['bv']}")
    elif meta.get("url"):
        lines.append(f"url: {meta['url']}")
    if meta.get("transcript_model"):
        lines.append(f"transcript_model: {_yaml_escape(meta['transcript_model'])}")
    if meta.get("asset_count") is not None:
        lines.append(f"asset_count: {meta['asset_count']}")
    if meta.get("assets"):
        lines.append("assets:")
        for asset in meta["assets"]:
            lines.append(f"  - {_yaml_escape(str(asset))}")

    # 简介：使用 | literal block scalar 保留换行
    if meta.get("description"):
        lines.append("description: |")
        for line in meta["description"].split("\n"):
            lines.append(f"  {line}")

    lines.append("---")
    lines.append("")  # frontmatter 结束的 blank line
    return "\n".join(lines)


def _yaml_escape(s: str) -> str:
    """YAML 字符串值的最小转义：含特殊字符时用双引号包裹

    处理边界：
      - 标题里的中文标点 `:` `#` `"` `'` 可能干扰 YAML 解析
      - 含这些字符的字符串用双引号包裹并转义内部双引号
    """
    if not s:
        return '""'
    # 含 YAML 特殊字符或首尾有空白 → 双引号
    if any(c in s for c in [':', '#', '"', "'", '\n', '\t']) or s != s.strip():
        escaped = s.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return s


def strip_existing_frontmatter(content: str) -> str:
    """若内容以 `---` 开头（含 frontmatter），剥离它

    处理边界：
      - 文件首部或中部的 BOM（U+FEFF）→ 全局清除
      - 多个堆叠的 frontmatter 块 → 全部剥掉（while 循环），实现幂等
      - 没有 frontmatter → 原样返回

    返回去掉 frontmatter 后的正文。
    """
    # 全局清 BOM（Python re 的 ^ 受 BOM 影响，文件中部也可能残留）
    content = content.replace(BOM, "")

    # 循环剥离：可能有多个堆叠（用户重复执行 / 旧 frontmatter 残留）
    while True:
        m = EXISTING_FM_RE.match(content)
        if not m:
            break
        content = content[m.end():]
    return content


def prepend_meta(meta: dict, subtitle_path: Path) -> bool:
    """把 meta 作为 frontmatter 插入到 subtitle_path 顶端

    返回 True = 成功，False = 失败（异常已 print 到 stderr）。
    """
    if not subtitle_path.exists():
        print(f"[prepend_meta] 字幕文件不存在: {subtitle_path}", file=sys.stderr)
        return False

    try:
        original = subtitle_path.read_text(encoding="utf-8")
    except Exception as e:
        print(f"[prepend_meta] 读取失败: {e}", file=sys.stderr)
        return False

    # 幂等：先剥离已有 frontmatter
    body = strip_existing_frontmatter(original)

    fm_text = build_frontmatter(meta)
    new_content = fm_text + body

    # 原子写：先写临时文件再 rename
    tmp_path = subtitle_path.with_suffix(subtitle_path.suffix + ".tmp")
    try:
        tmp_path.write_text(new_content, encoding="utf-8")
        tmp_path.replace(subtitle_path)
    except Exception as e:
        print(f"[prepend_meta] 写入失败: {e}", file=sys.stderr)
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except Exception:
                pass
        return False

    print(f"[prepend_meta] 已写入 frontmatter ({len(fm_text)} 字节) → {subtitle_path.name}")
    return True


def main():
    parser = argparse.ArgumentParser(
        description="把视频元数据（来自 bilibili-video-meta）作为 YAML frontmatter 拼到字幕文件顶端"
    )
    parser.add_argument("subtitle", help="字幕文件路径（.md / .srt）")
    parser.add_argument("--meta", metavar="JSON", help="meta JSON 字符串（来自 extract_meta.py 的 stdout）")
    parser.add_argument("--meta-file", metavar="FILE", help="从 JSON 文件读取 meta（与 --meta 二选一）")
    parser.add_argument("--meta-stdin", action="store_true", help="从 stdin 读取 meta JSON（一行）")
    args = parser.parse_args()

    # 解析 meta 来源
    meta_str: Optional[str] = None
    if args.meta:
        meta_str = args.meta
    elif args.meta_file:
        try:
            meta_str = Path(args.meta_file).read_text(encoding="utf-8").strip()
        except Exception as e:
            print(f"[prepend_meta] 读取 meta 文件失败: {e}", file=sys.stderr)
            sys.exit(2)
    elif args.meta_stdin or not sys.stdin.isatty():
        try:
            meta_str = sys.stdin.read().strip()
        except Exception as e:
            print(f"[prepend_meta] 读取 stdin 失败: {e}", file=sys.stderr)
            sys.exit(2)

    if not meta_str:
        print("[prepend_meta] 未提供 meta（用 --meta / --meta-file / --meta-stdin 之一）", file=sys.stderr)
        sys.exit(2)

    # 解析 JSON
    try:
        meta = json.loads(meta_str)
    except json.JSONDecodeError as e:
        print(f"[prepend_meta] meta JSON 解析失败: {e}", file=sys.stderr)
        sys.exit(2)

    # 兼容 extract_meta.py 的两种输出：
    #   - 单条模式：[ {...} ]    → 取首个
    #   - 直接传对象：{ ... }    → 直接用
    if isinstance(meta, list):
        if not meta:
            print("[prepend_meta] meta 数组为空", file=sys.stderr)
            sys.exit(2)
        meta = meta[0]
        if not isinstance(meta, dict):
            print(f"[prepend_meta] meta 数组首项不是 object（type={type(meta).__name__}）", file=sys.stderr)
            sys.exit(2)

    if not isinstance(meta, dict):
        print(f"[prepend_meta] meta 必须是 JSON object，实际类型: {type(meta).__name__}", file=sys.stderr)
        sys.exit(2)

    subtitle_path = Path(args.subtitle)
    ok = prepend_meta(meta, subtitle_path)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
