"""B站AI字幕提取器 - 支持单视频、UP主空间、收藏夹三种模式

使用 Selenium + JS Hook 拦截技术（与B站浏览器扩展相同）
"""
import os
import sys
import re
import time
import json
import subprocess
import argparse
from pathlib import Path
from typing import Optional

# 字幕输出目录（默认存到 00_Raw/01_B站视频转录/，与现有 .txt 转录稿同目录）
# scripts/ → bilibili-subtitle-fetch/ → skills/ → .claude/ → vault_root (5 层)
SUBTITLE_DIR = Path(__file__).parent.parent.parent.parent.parent / "00_Raw" / "01_B站视频转录"

# 本项目 wiki DB 路径（compile_db.json，video-wiki-compile 维护）
# scripts/ → bilibili-subtitle-fetch/ → skills/ → .claude/ → vault_root (5 层)
COMPILE_DB_PATH = Path(__file__).parent.parent.parent.parent.parent / "scripts" / "compile_db.json"

# 🆕 首次配置：字幕保存路径配置文件
# scripts/ → bilibili-subtitle-fetch/ → skills/ → .claude/ → vault_root (5 层)
CONFIG_PATH = Path(__file__).parent.parent / "config.json"


SUPPORTED_BROWSERS = ["edge", "chrome"]


def _load_config() -> dict:
    """加载配置文件，不存在或损坏返回空 dict"""
    if not CONFIG_PATH.exists():
        return {}
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_config(config: dict):
    """保存配置文件"""
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


def _first_run_config():
    """首次配置：询问字幕保存路径，写入 config.json"""
    print("=" * 50)
    print("首次配置 B站字幕提取工具")
    print("=" * 50)
    print(f"\n默认保存路径：{SUBTITLE_DIR}")
    custom = input("\n自定义保存路径（直接回车使用默认路径）: ").strip()
    path = Path(custom) if custom else SUBTITLE_DIR
    config = _load_config()
    config["output_dir"] = str(path.resolve())
    _save_config(config)
    print(f"\n✅ 配置已保存：{path.resolve()}")
    print("  后续下载将默认保存到此处")
    print("  如需更改，可删除或修改：.claude/skills/bilibili-subtitle-fetch/config.json")
    print("=" * 50)
    return path


class SubtitleExtractor:
    def __init__(self, output_dir: Path = None, cookies_path: str = None, compile_db_path: str = None, enrich_with_meta: bool = True, browser: str = "chrome"):
        # 🆕 优先级：显式参数 > 配置文件 > SUBTITLE_DIR
        if output_dir is None:
            config = _load_config()
            saved_dir = config.get("output_dir")
            output_dir = Path(saved_dir) if saved_dir else SUBTITLE_DIR
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.cookies_path = cookies_path
        # 🆕 本项目 wiki DB（compile_db.json）：命中则跳过下载
        self.compile_db_path = Path(compile_db_path) if compile_db_path else COMPILE_DB_PATH
        self._compile_db_ids = None  # 懒加载
        # 🆕 v1.2 字幕保存后是否自动拼元数据 frontmatter（默认 True；--no-meta 关闭）
        self.enrich_with_meta = enrich_with_meta
        # 🆕 浏览器选择（默认 chrome；支持 edge / chrome）
        self.browser = browser.lower()
        if self.browser not in SUPPORTED_BROWSERS:
            raise ValueError(f"不支持的浏览器: {browser}，仅支持: {SUPPORTED_BROWSERS}")

    def _load_compile_db_ids(self) -> set:
        """懒加载：compile_db.json 里所有已记录的 ID（id 字段）"""
        if self._compile_db_ids is not None:
            return self._compile_db_ids
        self._compile_db_ids = set()
        if not self.compile_db_path or not self.compile_db_path.exists():
            return self._compile_db_ids
        try:
            with open(self.compile_db_path, encoding='utf-8') as f:
                data = json.load(f)
            for r in data.get('records', []):
                vid = r.get('id')
                if vid:
                    self._compile_db_ids.add(vid)
            print(f"  [wiki DB] 已加载 {len(self._compile_db_ids)} 条 ID 记录: {self.compile_db_path.name}")
        except Exception as e:
            print(f"  [警告] 读取 compile_db.json 失败: {e}")
        return self._compile_db_ids

    def to_srt_time(self, sec: float) -> str:
        """将秒数转换为 SRT 时间格式"""
        ms = int(sec * 1000)
        h = ms // 3600000
        m = (ms % 3600000) // 60000
        s = (ms % 60000) // 1000
        milli = ms % 1000
        return f"{h:02d}:{m:02d}:{s:02d},{milli:03d}"

    def save_srt(self, body: list, filename: str) -> bool:
        """保存字幕为 SRT 格式"""
        if not body:
            print("  [警告] 空字幕数据")
            return False
        srt_lines = []
        idx = 1
        for item in body:
            text = (item.get("content") or "").strip()
            if not text:
                continue
            start = item.get("from", 0)
            end = item.get("to", start + 2)
            srt_lines.append(f"{idx}\n{self.to_srt_time(start)} --> {self.to_srt_time(end)}\n{text}\n")
            idx += 1
        with open(filename, "w", encoding="utf-8") as f:
            f.write("\n".join(srt_lines))
        # 兼容 filename 是 str 或 Path
        display_name = Path(filename).name
        print(f"  [保存] {idx - 1} 条字幕 -> {display_name}")
        return True

    def is_subtitle_exists(self, bv_id: str) -> bool:
        """检查是否应跳过此视频（2 个来源：磁盘 / 本项目 wiki DB）"""
        return self.get_skip_reason(bv_id) is not None

    def get_skip_reason(self, bv_id: str) -> Optional[str]:
        """返回跳过原因（用于日志），None = 不跳过"""
        # 1. 磁盘上 .md 已存在
        subtitle_path = self.output_dir / f"{bv_id}.md"
        if subtitle_path.exists():
            return f"磁盘已存在 {subtitle_path.name}"
        # 2. 本项目 wiki DB (compile_db.json) 已有此 ID
        if bv_id in self._load_compile_db_ids():
            return f"wiki DB 已有此 ID（compile_db.json）"
        return None

    def extract_single(self, bvid: str, retry: bool = True) -> Optional[str]:
        """提取单个视频的AI字幕

        Args:
            bvid: BV号
            retry: 是否在失败时重试

        Returns:
            字幕文件路径，失败返回 None
        """
        # 检查是否应跳过（磁盘 / wiki DB 两个来源）
        reason = self.get_skip_reason(bvid)
        if reason:
            print(f"  [跳过] {bvid} - {reason}")
            return "SKIPPED"

        print(f"  [提取] {bvid}")
        video_url = f"https://www.bilibili.com/video/{bvid}"

        # 关闭浏览器
        browser_process_map = {
            "edge": "msedge.exe",
            "chrome": "chrome.exe",
        }
        proc_name = browser_process_map[self.browser]
        print(f"  [浏览器] 关闭 {self.browser}...")
        subprocess.run(f'taskkill /F /IM {proc_name} 2>nul', shell=True)
        time.sleep(2)

        from selenium import webdriver

        driver = None
        try:
            if self.browser == "edge":
                from selenium.webdriver.edge.options import Options
                user_data_dir = os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Edge\User Data")
                options = Options()
                options.add_argument(f"--user-data-dir={user_data_dir}")
                options.add_argument("--profile-directory=Default")
                print("  [浏览器] 启动 Edge...")
                driver = webdriver.Edge(options=options)
            elif self.browser == "chrome":
                from selenium.webdriver.chrome.options import Options
                user_data_dir = os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\User Data")
                options = Options()
                options.add_argument(f"--user-data-dir={user_data_dir}")
                options.add_argument("--profile-directory=Default")
                print("  [浏览器] 启动 Chrome...")
                driver = webdriver.Chrome(options=options)

            print(f"  [页面] 打开: {video_url}")
            driver.get(video_url)

            print("  [等待] 播放器加载 (8s)...")
            time.sleep(8)

            # 注入 JS Hook
            print("  [注入] Extension-style JS Hook...")
            driver.execute_script("""
                window.__capturedSubtitleUrl = null;
                window.__capturedSubtitleData = null;

                const origFetch = window.fetch;
                window.fetch = function(...args) {
                    const url = typeof args[0] === 'string' ? args[0] : (args[0] && args[0].url);
                    if (url && (url.includes('aisubtitle.hdslb.com') || url.includes('ai_subtitle'))) {
                        window.__capturedSubtitleUrl = url;
                        console.log('[SUB] Fetch:', url);
                    }
                    return origFetch.apply(this, args);
                };

                const origXHROpen = window.XMLHttpRequest.prototype.open;
                window.XMLHttpRequest.prototype.open = function(method, url, ...rest) {
                    if (url && (url.includes('aisubtitle.hdslb.com') || url.includes('ai_subtitle'))) {
                        this.__ai_url = url;
                        window.__capturedSubtitleUrl = url;
                        console.log('[SUB] XHR open:', url);
                    }
                    return origXHROpen.call(this, method, url, ...rest);
                };

                const origXHRSend = window.XMLHttpRequest.prototype.send;
                window.XMLHttpRequest.prototype.send = function(...args) {
                    const self = this;
                    if (self.__ai_url) {
                        self.addEventListener('load', function() {
                            try {
                                const text = self.responseText;
                                const json = JSON.parse(text);
                                if (json && json.body && Array.isArray(json.body) && json.body.length > 0) {
                                    const one = json.body[0];
                                    if ('from' in one && 'to' in one && 'content' in one) {
                                        window.__capturedSubtitleData = json;
                                        window.__capturedSubtitleUrl = self.__ai_url;
                                        console.log('[SUB] Captured:', json.body.length, 'entries');
                                    }
                                }
                            } catch(e) {
                                console.log('[SUB] Parse error:', e.message);
                            }
                        });
                    }
                    return origXHRSend.apply(this, args);
                };
                console.log('[SUB] Hooks installed');
            """)

            # 点击字幕按钮
            print("  [操作] 点击字幕按钮...")
            try:
                driver.execute_script("""
                    const btn = document.querySelector('.bpx-player-ctrl-btn.bpx-player-ctrl-subtitle') ||
                               document.querySelector('.bpx-player-ctrl-subtitle');
                    if (btn) {
                        btn.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true}));
                        console.log('[SUB] Subtitle btn clicked');
                    }
                """)
                time.sleep(2)
            except Exception as e:
                print(f"  [错误] 点击字幕按钮: {e}")

            # 选择第一个语言项
            print("  [操作] 选择字幕语言...")
            try:
                driver.execute_script("""
                    const items = Array.from(document.querySelectorAll('.bpx-player-ctrl-subtitle-language-item-text'));
                    if (items && items.length > 0) {
                        const target = items[0];
                        console.log('[SUB] Clicking:', target.textContent.trim());
                        target.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true}));
                    } else {
                        console.log('[SUB] No language items');
                    }
                """)
                time.sleep(5)
            except Exception as e:
                print(f"  [错误] 选择语言: {e}")

            # 检查捕获的数据
            print("  [检查] 捕获字幕数据...")
            url = driver.execute_script("return window.__capturedSubtitleUrl")
            data = driver.execute_script("return window.__capturedSubtitleData")

            if data and data.get("body"):
                body = data["body"]
                output_path = self.output_dir / f"{bvid}.md"
                if self.save_srt(body, str(output_path)):
                    # 更新数据库
                    self._update_db_subtitle_path(bvid, str(output_path))
                    # 🆕 v1.2 拼元数据 frontmatter（best-effort，不阻塞主流程）
                    if self.enrich_with_meta:
                        self._enrich_with_meta(output_path, bvid)
                    return str(output_path)
            else:
                print(f"  [警告] 未捕获到字幕 (url={bool(url)}, data={bool(data)})")
                if retry:
                    print("  [重试] 第一次失败，重新尝试...")
                    time.sleep(3)
                    return self.extract_single(bvid, retry=False)

            return None

        except Exception as e:
            print(f"  [错误] 提取失败: {e}")
            if retry:
                print("  [重试] 出错重试...")
                time.sleep(3)
                return self.extract_single(bvid, retry=False)
            return None

        finally:
            if driver:
                driver.quit()

    def _update_db_subtitle_path(self, bv_id: str, subtitle_path: str):
        """更新本项目 wiki DB (compile_db.json) 的 subtitle_srt_path 字段

        仅在已有 record 的情况下追加字段；不在的 id 不创建（让 wiki compile 流程处理）
        """
        if not self.compile_db_path or not self.compile_db_path.exists():
            return
        try:
            with open(self.compile_db_path, encoding='utf-8') as f:
                data = json.load(f)
            updated = False
            for r in data.get('records', []):
                if r.get('id') == bv_id:
                    r['subtitle_srt_path'] = str(subtitle_path)
                    r['subtitle_downloaded_at'] = time.strftime("%Y-%m-%d %H:%M:%S")
                    updated = True
                    break
            if updated:
                with open(self.compile_db_path, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                print(f"  [wiki DB] 更新 {bv_id} 的 subtitle_srt_path")
            else:
                print(f"  [wiki DB] {bv_id} 不在 records 中，跳过更新（wiki compile 流程会处理）")
        except Exception as e:
            print(f"  [wiki DB] 更新失败: {e}")

    def _enrich_with_meta(self, subtitle_path: Path, bvid: str) -> None:
        """🆕 v1.3 调用 extract_meta 抓取元数据并拼到字幕顶端

        通过 subprocess 调用两个独立脚本：
          1. extract_meta.py → JSON（优先 bundled，回退外部）
          2. prepend_meta.py → frontmatter（同目录 bundled）

        路径查找顺序（v1.3 vendor 策略）：
          1. bundled: scripts/extract_meta.py（v1.3+ 仓库自带，独立可运行）
          2. 外部:   ../bilibili-video-meta/scripts/extract_meta.py（旧版兼容）

        容错：元数据抓取失败不影响字幕下载主流程（best-effort enrichment）。
        """
        # 1. bundled 路径（vendor 副本，v1.3+ 默认）
        bundled_extract = Path(__file__).parent / "extract_meta.py"
        # 2. 外部路径（兼容老版本 vault 结构）
        skills_root = Path(__file__).parent.parent.parent.parent  # scripts/ → skill/ → skills/ → .claude/ → vault
        external_extract = skills_root / "bilibili-video-meta" / "scripts" / "extract_meta.py"

        if bundled_extract.exists():
            extract_meta_script = bundled_extract
            meta_source = "bundled"
        elif external_extract.exists():
            extract_meta_script = external_extract
            meta_source = "external (../bilibili-video-meta/)"
        else:
            print(
                f"  [meta] 跳过：找不到 extract_meta.py（bundled 或 ../bilibili-video-meta/）",
                file=sys.stderr,
            )
            return

        prepend_meta_script = Path(__file__).parent / "prepend_meta.py"
        if not prepend_meta_script.exists():
            print(f"  [meta] 跳过：找不到 {prepend_meta_script}", file=sys.stderr)
            return

        print(f"  [meta] 来源: {meta_source}")

        try:
            print(f"  [meta] 抓取元数据 → {bvid}")
            meta_proc = subprocess.run(
                [sys.executable, str(extract_meta_script), bvid, "--indent", "0"],
                capture_output=True, text=True, timeout=30,
            )
            if meta_proc.returncode != 0:
                print(f"  [meta] 抓取失败（rc={meta_proc.returncode}）: {meta_proc.stderr.strip()[:200]}")
                return
            meta_json = meta_proc.stdout.strip()
            if not meta_json:
                print("  [meta] 抓取返回空，跳过")
                return

            # extract_meta 在单条模式下也返回 JSON 数组 [{...}]，unwrap 到首个对象
            import json as _json
            parsed = _json.loads(meta_json)
            if isinstance(parsed, list):
                if not parsed:
                    print("  [meta] 抓取返回空数组，跳过")
                    return
                meta_obj = parsed[0]
                if not isinstance(meta_obj, dict):
                    print(f"  [meta] 数组首项不是对象（type={type(meta_obj).__name__}），跳过")
                    return
            elif isinstance(parsed, dict):
                meta_obj = parsed
            else:
                print(f"  [meta] 返回非 dict/list（type={type(parsed).__name__}），跳过")
                return
            meta_json = _json.dumps(meta_obj, ensure_ascii=False)
        except subprocess.TimeoutExpired:
            print("  [meta] 抓取超时（30s），跳过")
            return
        except Exception as e:
            print(f"  [meta] 抓取异常: {e}")
            return

        try:
            print(f"  [meta] 拼 frontmatter → {subtitle_path.name}")
            prepend_proc = subprocess.run(
                [sys.executable, str(prepend_meta_script), str(subtitle_path), "--meta", meta_json],
                capture_output=True, text=True, timeout=10,
            )
            if prepend_proc.returncode != 0:
                print(f"  [meta] 拼入失败（rc={prepend_proc.returncode}）: {prepend_proc.stderr.strip()[:200]}")
            else:
                # 透传 prepend_meta 的进度信息
                for line in prepend_proc.stdout.strip().splitlines():
                    print(f"  [meta] {line}")
        except subprocess.TimeoutExpired:
            print("  [meta] 拼入超时（10s）")
        except Exception as e:
            print(f"  [meta] 拼入异常: {e}")

    def extract_space(self, space_url: str) -> list:
        """提取UP主空间所有视频的字幕

        Args:
            space_url: UP主上传视频页面URL
            例如: https://space.bilibili.com/3546663834618256/upload/video
        """
        print(f"[空间字幕] 开始提取: {space_url}")

        # 提取 UID
        match = re.search(r'space\.bilibili\.com/(\d+)', space_url)
        if not match:
            print("  [错误] 无法从URL提取UID")
            return []
        uid = match.group(1)

        # 使用 yt-dlp 获取视频列表
        print("  [获取] 视频列表...")
        try:
            import yt_dlp

            ydl_opts = {
                'cookiefile': self.cookies_path or 'cookies.txt',
                'quiet': True,
                'skip_download': True,
                'flat_playlist': True,
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                result = ydl.extract_info(space_url, download=False)
                entries = result.get('entries') or []
                print(f"  [获取] 找到 {len(entries)} 个视频")
        except Exception as e:
            print(f"  [错误] 获取视频列表失败: {e}")
            return []

        # 逐个提取字幕
        results = []
        for i, entry in enumerate(entries):
            bv_id = entry.get('id', '')
            if not bv_id:
                continue
            print(f"  [{i+1}/{len(entries)}] 处理 {bv_id}")
            subtitle_path = self.extract_single(bv_id)
            is_skipped = subtitle_path == "SKIPPED"
            results.append({
                'bv_id': bv_id,
                'title': entry.get('title', ''),
                'subtitle_path': None if is_skipped else subtitle_path,
                'success': subtitle_path not in (None, "SKIPPED"),
                'skipped': is_skipped,
            })
            time.sleep(2)  # 避免请求过快

        return results

    def extract_favorites(self, favorites_file: str = None) -> list:
        """提取收藏夹所有视频的字幕

        Args:
            favorites_file: 收藏夹JSON文件路径
        """
        if favorites_file is None:
            favorites_file = Path(__file__).parent.parent.parent.parent / "videos_fav.json"

        print(f"[收藏夹字幕] 读取: {favorites_file}")
        try:
            with open(favorites_file, "r", encoding="utf-8") as f:
                videos = json.load(f)
            if isinstance(videos, list):
                print(f"  [获取] 找到 {len(videos)} 个视频")
            else:
                print("  [错误] 文件格式错误")
                return []
        except FileNotFoundError:
            print(f"  [错误] 文件不存在: {favorites_file}")
            print("  请先准备 videos_fav.json（B站收藏夹导出的视频列表）")
            return []
        except json.JSONDecodeError:
            print("  [错误] JSON解析失败")
            return []

        # 逐个提取字幕
        results = []
        for i, video in enumerate(videos):
            bv_id = video.get('bv_id', '')
            title = video.get('title', '')
            if not bv_id:
                continue
            print(f"  [{i+1}/{len(videos)}] {bv_id} - {title[:30]}...")
            subtitle_path = self.extract_single(bv_id)
            is_skipped = subtitle_path == "SKIPPED"
            results.append({
                'bv_id': bv_id,
                'title': title,
                'subtitle_path': None if is_skipped else subtitle_path,
                'success': subtitle_path not in (None, "SKIPPED"),
                'skipped': is_skipped,
            })
            time.sleep(2)  # 避免请求过快

        return results


def main():
    parser = argparse.ArgumentParser(description='B站AI字幕提取工具')
    parser.add_argument('bvid', nargs='?', help='BV号或视频URL（单视频模式）')
    parser.add_argument('--space', metavar='URL', help='UP主空间URL（空间模式）')
    parser.add_argument('--favorites', action='store_true', help='收藏夹模式')
    parser.add_argument('--output', '-o', metavar='DIR', default=None, help='输出目录')
    parser.add_argument('--no-meta', action='store_true', help='🆕 跳过字幕顶端拼元数据 frontmatter')
    parser.add_argument('--browser', default=None, choices=['chrome', 'edge'],
                        help='浏览器类型（默认 chrome，省略则交互选择）')
    parser.add_argument('--reset-config', action='store_true', help='🆕 重置配置文件，重新引导首次配置')

    args = parser.parse_args()

    # 🆕 重置配置
    if args.reset_config:
        if CONFIG_PATH.exists():
            CONFIG_PATH.unlink()
            print(f"已删除配置文件：{CONFIG_PATH}")
        _first_run_config()
        config = _load_config()
    else:
        config = _load_config()
        # 🆕 首次配置检测（未指定 --output 且 config.json 无 output_dir 时触发）
        if not args.output and not config.get("output_dir"):
            _first_run_config()
            config = _load_config()  # 重新加载以获取新保存的路径

    # 交互选择浏览器（省略 --browser 时触发）
    browser = args.browser
    if browser is None:
        print("请选择浏览器（已登录 B站 的那个）：")
        print("  1) Chrome")
        print("  2) Edge")
        choice = input("输入选项 [1]: ").strip()
        browser = "chrome" if choice in ("", "1") else "edge"

    extractor = SubtitleExtractor(
        output_dir=Path(args.output) if args.output else None,
        enrich_with_meta=not args.no_meta,
        browser=browser,
    )

    # 模式判断
    if args.favorites:
        print("=" * 50)
        print("模式: 收藏夹字幕")
        print("=" * 50)
        results = extractor.extract_favorites()
        success = sum(1 for r in results if r['success'])
        print(f"\n完成: {success}/{len(results)} 成功")

    elif args.space:
        print("=" * 50)
        print("模式: UP主空间字幕")
        print("=" * 50)
        results = extractor.extract_space(args.space)
        success = sum(1 for r in results if r['success'])
        print(f"\n完成: {success}/{len(results)} 成功")

    elif args.bvid:
        print("=" * 50)
        print("模式: 单视频字幕")
        print("=" * 50)
        # 提取BV号
        bvid = args.bvid
        if 'bilibili.com/video/' in bvid:
            match = re.search(r'/video/(BV[\w]+)', bvid)
            if match:
                bvid = match.group(1)
        elif not bvid.startswith('BV'):
            print(f"[错误] 无效的BV号: {bvid}")
            sys.exit(1)

        result = extractor.extract_single(bvid)
        if result == "SKIPPED":
            print("\n跳过: 已在 DB 或磁盘中存在")
            sys.exit(0)
        elif result:
            print(f"\n成功: {result}")
            sys.exit(0)
        else:
            print("\n失败: 未能提取字幕")
            sys.exit(1)

    else:
        parser.print_help()
        print("\n示例:")
        print("  python subtitle_extractor.py BV11RffBdEEQ")
        print("  python subtitle_extractor.py --space https://space.bilibili.com/3546663834618256/upload/video")
        print("  python subtitle_extractor.py --favorites")
        sys.exit(1)


if __name__ == "__main__":
    main()