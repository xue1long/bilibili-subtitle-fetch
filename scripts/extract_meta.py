#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bilibili-video-meta / extract_meta.py

从 B站 视频页 HTML 提取 6 个核心元数据字段：
  - title              (视频原标题，来自 videoData.title)
  - uploader           (UP 主名，来自 videoData.owner.name)
  - view_count         (播放量，来自 videoData.stat.view)
  - like_count         (点赞数，来自 videoData.stat.like)
  - description        (视频简介，来自 videoData.desc；可能含换行，需用 literal block scalar)
  - video_published_at (发布时间，来自 videoData.pubdate，Unix → YYYY-MM-DD)

支持：
  - 单个 BV号：  python extract_meta.py BV1S7zEBcELo
  - 完整 URL：   python extract_meta.py "https://www.bilibili.com/video/BV1S7zEBcELo"
  - 批量模式：   python extract_meta.py --input bvs.txt
  - 输出到文件： python extract_meta.py BVxxx --output result.json
  - 管道友好：  默认输出 JSON 到 stdout
  - 增量保存：  --save-ok <file> 每条成功后追加，避免中途崩溃丢数据
  - 断点续跑：  --skip-existing <file> 已成功的跳过

🆕 v1.2 变更：
  - 新增 view_count（stat.view）和 description（videoData.desc）字段
  - 4 字段 → 6 字段，覆盖视频笔记最常用的全部元信息
  - 字段顺序调整：view_count 紧跟 uploader 之后（与 stat 块字段顺序一致）

防爬机制：
  - 浏览器 UA + Accept-Language
  - 批量请求间隔 = base ± jitter 随机抖动（默认 0.8-1.5s）
  - 遇到 412/429/5xx 自动指数退避重试（最多 3 次）
"""
import argparse
import io
import json
import random
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Windows GBK 控制台兼容：强制 UTF-8 输出
if sys.platform == "win32" and __name__ == "__main__":
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    except Exception:
        pass

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
URL_PATTERN = re.compile(r"https?://(?:www\.)?bilibili\.com/video/(BV[\w]+)")


def normalize_bv(s: str) -> str:
    """从 URL/BV号/混合输入里提取 BV 号"""
    s = s.strip()
    m = URL_PATTERN.search(s)
    if m:
        return m.group(1)
    if s.upper().startswith("BV"):
        return s
    raise ValueError(f"无法识别为 BV号 或 B站 URL: {s!r}")


def fetch_html(bv: str, timeout: int = 15, max_retries: int = 3) -> str:
    """抓 B站 视频页 HTML（无需登录；自动处理 gzip；带重试）

    防爬机制：
    - 浏览器级 UA + Accept-Language
    - 412/429/5xx 自动指数退避重试（1s → 2s → 4s）
    - Connection: keep-alive 复用 TCP
    """
    url = f"https://www.bilibili.com/video/{bv}"
    headers = {
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
    }
    last_err = None
    for attempt in range(max_retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                ce = resp.headers.get("Content-Encoding", "").lower()
                if "gzip" in ce:
                    import gzip
                    raw = gzip.decompress(raw)
                elif "deflate" in ce:
                    import zlib
                    raw = zlib.decompress(raw)
                return raw.decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            last_err = e
            # 412/429/5xx 才重试；404/403 不重试（视频不存在/被删）
            if e.code in (412, 429) or 500 <= e.code < 600:
                if attempt < max_retries - 1:
                    wait = 2 ** attempt  # 1, 2, 4 秒
                    print(f"    [retry {attempt+1}/{max_retries-1}] HTTP {e.code}, 等 {wait}s", file=sys.stderr)
                    time.sleep(wait)
                    continue
            raise RuntimeError(f"HTTP {e.code} {e.reason} 抓取 {url} 失败") from e
        except urllib.error.URLError as e:
            last_err = e
            if attempt < max_retries - 1:
                wait = 2 ** attempt
                print(f"    [retry {attempt+1}/{max_retries-1}] URL error: {e.reason}, 等 {wait}s", file=sys.stderr)
                time.sleep(wait)
                continue
            raise RuntimeError(f"网络错误抓取 {url} 失败: {e.reason}") from e
    raise RuntimeError(f"抓取 {url} 重试 {max_retries} 次仍失败: {last_err}")


def extract_video_data_block(html: str) -> str:
    """从 HTML 抓出 window.__INITIAL_STATE__ 里 videoData {...} 块"""
    m = re.search(r"window\.__INITIAL_STATE__\s*=\s*(\{.+?\});\(function", html, re.DOTALL)
    if not m:
        raise RuntimeError("页面里没找到 window.__INITIAL_STATE__（可能被反爬或页面结构变了）")
    js = m.group(1)
    vd = re.search(r'"videoData"\s*:\s*\{', js)
    if not vd:
        raise RuntimeError("__INITIAL_STATE__ 里没找到 videoData 块")
    start = vd.end() - 1
    depth = 0
    end = start
    for i in range(start, min(start + 200000, len(js))):
        if js[i] == "{":
            depth += 1
        elif js[i] == "}":
            depth -= 1
            if depth == 0:
                end = i
                break
    return js[start:end + 1]


def _build_meta(
    bv: str,
    title: Optional[str],
    uploader: Optional[str],
    view_count: Optional[int],
    like_count: Optional[int],
    description: Optional[str],
    pubdate: Optional[int],
) -> dict:
    return {
        "bv": bv,
        "title": title,
        "uploader": uploader,
        "view_count": view_count,
        "like_count": like_count,
        "description": description,
        "video_published_at": unix_to_date(pubdate),
    }


def get_str(block: str, key: str) -> Optional[str]:
    """从块里提 "key": "value" 字符串值

    注意：B 站 HTML 通常以 UTF-8 编码，Chinese chars 已是正常 unicode。
    但 description / title 等长文本里常含字面 \\uXXXX（被 B 站强制转义为 ASCII 安全形式）
    和字面 \\n（保留为转义形式）。需要把这两种都解码成真实字符。

    边界：
      - 值里只有 ASCII（如纯英文）→ val.encode/decode("unicode_escape") 能搞定
      - 值里混中文（unicode） + ASCII 转义 → 旧版 encode/ascii 会炸，改用正则替换
    """
    m = re.search(rf'"{re.escape(key)}"\s*:\s*"((?:[^"\\]|\\.)*)"', block)
    if not m:
        return None
    val = m.group(1)

    # 优先尝试 unicode_escape（整串 ASCII 时最稳）
    if r"\u" in val or r"\n" in val or r"\t" in val:
        # 先尝试全 ASCII 路径
        try:
            decoded = val.encode("ascii").decode("unicode_escape")
            return decoded
        except (UnicodeDecodeError, UnicodeEncodeError):
            pass
        # 混合 unicode 路径：仅替换 \uXXXX 和 \n/\t/\r（其他字符保留原样）
        val = re.sub(r"\\u([0-9a-fA-F]{4})", lambda mm: chr(int(mm.group(1), 16)), val)
        val = val.replace("\\n", "\n").replace("\\t", "\t").replace("\\r", "\r")
        val = val.replace('\\"', '"').replace("\\\\", "\\")
    return val


def get_int(block: str, key: str) -> Optional[int]:
    """从块里提 "key": int 数值"""
    m = re.search(rf'"{re.escape(key)}"\s*:\s*(-?\d+)', block)
    return int(m.group(1)) if m else None


def get_object(block: str, key: str) -> Optional[str]:
    """从块里提 "key": { ... } 子块原文"""
    m = re.search(rf'"{re.escape(key)}"\s*:\s*\{{', block)
    if not m:
        return None
    start = m.end() - 1
    depth = 0
    end = start
    for i in range(start, min(start + 20000, len(block))):
        if block[i] == "{":
            depth += 1
        elif block[i] == "}":
            depth -= 1
            if depth == 0:
                end = i
                break
    return block[start:end + 1]


def unix_to_date(ts: Optional[int]) -> Optional[str]:
    """Unix timestamp → YYYY-MM-DD (UTC)"""
    if ts is None:
        return None
    try:
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
    except (ValueError, OSError):
        return None


def extract_meta_from_html(bv: str, html: str) -> dict:
    """从已经取得的视频页 HTML 提取元数据。"""
    vd_block = extract_video_data_block(html)

    # title 来自 videoData.title
    title = get_str(vd_block, "title")

    # uploader 来自 owner.name
    owner_block = get_object(vd_block, "owner") or ""
    uploader = get_str(owner_block, "name")

    # view_count / like_count 来自 stat.view / stat.like
    stat_block = get_object(vd_block, "stat") or ""
    view_count = get_int(stat_block, "view")
    like_count = get_int(stat_block, "like")

    # description 来自 videoData.desc（可能含换行和 \n）
    description = get_str(vd_block, "desc")

    # video_published_at 来自 pubdate
    pubdate = get_int(vd_block, "pubdate")
    return _build_meta(bv, title, uploader, view_count, like_count, description, pubdate)


def extract_meta_from_state(bv: str, state: dict) -> dict:
    """从浏览器运行时的 window.__INITIAL_STATE__ 提取元数据。"""
    video_data = state.get("videoData") if isinstance(state, dict) else None
    if not isinstance(video_data, dict):
        raise RuntimeError("浏览器运行时没有 videoData")

    owner = video_data.get("owner") or {}
    stat = video_data.get("stat") or {}
    return _build_meta(
        bv,
        video_data.get("title"),
        owner.get("name") if isinstance(owner, dict) else None,
        stat.get("view") if isinstance(stat, dict) else None,
        stat.get("like") if isinstance(stat, dict) else None,
        video_data.get("desc"),
        video_data.get("pubdate"),
    )


def extract_meta(bv: str) -> dict:
    """主入口：BV 号 → {title, uploader, view_count, like_count, description, video_published_at}"""
    return extract_meta_from_html(bv, fetch_html(bv))


def main():
    parser = argparse.ArgumentParser(
        description="从 B站 视频页 HTML 提取 6 个元数据字段（title / uploader / view_count / like_count / description / video_published_at）"
    )
    parser.add_argument("bv", nargs="?", help="BV号 或 B站视频 URL（单条模式）")
    parser.add_argument("--input", "-i", metavar="FILE", help="批量模式：每行一个 BV号 或 URL")
    parser.add_argument("--output", "-o", metavar="FILE", help="最终输出到文件（默认 stdout）")
    parser.add_argument("--save-ok", metavar="FILE", help="每条成功后追加到 JSON-Lines 文件（防中途崩溃丢数据）")
    parser.add_argument("--skip-existing", metavar="FILE", help="跳过此 JSON-Lines 文件里已成功的 BV（断点续跑）")
    parser.add_argument("--timeout", type=int, default=15, help="HTTP 超时秒数（默认 15）")
    parser.add_argument("--delay-min", type=float, default=0.8, help="请求最小间隔秒（默认 0.8）")
    parser.add_argument("--delay-max", type=float, default=1.5, help="请求最大间隔秒（默认 1.5）")
    parser.add_argument("--indent", type=int, default=2, help="JSON 缩进（默认 2，0 = 单行）")
    args = parser.parse_args()

    if not args.bv and not args.input:
        parser.print_help()
        print("\n示例:")
        print("  python extract_meta.py BV1S7zEBcELo")
        print("  python extract_meta.py 'https://www.bilibili.com/video/BV1S7zEBcELo'")
        print("  python extract_meta.py --input bvs.txt --output results.json --save-ok ok.jsonl")
        print("  python extract_meta.py --input bvs.txt --output results.json --skip-existing ok.jsonl  # 断点续跑")
        sys.exit(1)

    # 解析输入列表
    if args.input:
        in_path = Path(args.input)
        if not in_path.exists():
            print(f"[错误] 输入文件不存在: {in_path}", file=sys.stderr)
            sys.exit(1)
        items = [line.strip() for line in in_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    else:
        items = [args.bv]

    # 断点续跑：读取已成功的 BV 集合
    skip_set = set()
    if args.skip_existing and Path(args.skip_existing).exists():
        for line in Path(args.skip_existing).read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
                if r.get("bv"):
                    skip_set.add(r["bv"])
            except (json.JSONDecodeError, KeyError):
                continue
        print(f"[断点续跑] 已跳过 {len(skip_set)} 个已成功的 BV", file=sys.stderr)

    # 打开增量保存文件（追加模式）
    save_fh = None
    if args.save_ok:
        save_fh = open(args.save_ok, "a", encoding="utf-8")
        print(f"[增量保存] 成功记录追加到 {args.save_ok}", file=sys.stderr)

    results = []
    for i, raw in enumerate(items, 1):
        try:
            bv = normalize_bv(raw)
        except ValueError as e:
            print(f"[{i}/{len(items)}] [跳过] {e}", file=sys.stderr)
            results.append({"input": raw, "error": str(e)})
            continue

        if bv in skip_set:
            print(f"[{i}/{len(items)}] {bv} 已在 skip 集合中，跳过", file=sys.stderr)
            results.append({"bv": bv, "skipped": True})
            continue

        try:
            meta = extract_meta(bv)
            # 进度日志：只打印 BV + 成功标记（meta 含中文，Windows GBK 控制台会崩）
            print(f"[{i}/{len(items)}] {bv} OK (likes={meta.get('like_count')})", file=sys.stderr)
            results.append(meta)
            if save_fh:
                save_fh.write(json.dumps(meta, ensure_ascii=False) + "\n")
                save_fh.flush()
        except Exception as e:
            err_str = str(e).encode("ascii", errors="replace").decode("ascii")
            print(f"[{i}/{len(items)}] [失败] {bv}: {err_str}", file=sys.stderr)
            results.append({"bv": bv, "error": str(e)})
            if save_fh:
                save_fh.write(json.dumps({"bv": bv, "error": str(e)}, ensure_ascii=False) + "\n")
                save_fh.flush()

        # 抖动 sleep（最后一条不睡）
        if i < len(items):
            delay = random.uniform(args.delay_min, args.delay_max)
            time.sleep(delay)

    if save_fh:
        save_fh.close()

    # 输出
    indent = args.indent if args.indent > 0 else None
    output_text = json.dumps(results, ensure_ascii=False, indent=indent)
    if args.output:
        Path(args.output).write_text(output_text, encoding="utf-8")
        success = sum(1 for r in results if "title" in r)
        fail = sum(1 for r in results if "error" in r)
        print(f"\n[完成] 总 {len(results)} 条（成功 {success} / 失败 {fail}）写入 {args.output}", file=sys.stderr)
    else:
        print(output_text)

    if any("error" in result for result in results):
        sys.exit(1)


if __name__ == "__main__":
    main()
