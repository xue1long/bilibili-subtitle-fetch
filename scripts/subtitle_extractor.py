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
from runtime_paths import (
    chrome_profile_dir,
    compile_db_path,
    default_output_dir,
    edge_profile_dir,
    favorites_path,
)
from bili_guard import (
    CircuitBreaker,
    FailureKind,
    RateLimiter,
    check_login_state,
    classify_failure,
    download_list_lock,
    install_subtitle_probe,
    normalize_subtitle_probe,
    read_subtitle_probe,
)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# 字幕输出目录（默认存到项目根目录下的 10_raw/01_B站视频转录/）
SUBTITLE_DIR = default_output_dir()

# 本项目 wiki DB 路径（compile_db.json，video-wiki-compile 维护）
COMPILE_DB_PATH = compile_db_path()

# 🆕 首次配置：字幕保存路径配置文件
CONFIG_PATH = Path(__file__).parent.parent / "config.json"

# 下载列表文件名
DOWNLOAD_LIST_FILENAME = "download_list.json"
PAGE_LOAD_TIMEOUT_SECONDS = 30


SUPPORTED_BROWSERS = ["edge", "chrome"]


def _subtitle_body_from_payload(payload):
    body = payload.get("body") if isinstance(payload, dict) else None
    if not isinstance(body, list) or not body:
        return None
    valid = [item for item in body if isinstance(item, dict) and "content" in item]
    return valid or None


def _click_subtitle_button(driver):
    return driver.execute_script("""
        const btn = document.querySelector('.bpx-player-ctrl-btn.bpx-player-ctrl-subtitle') ||
                   document.querySelector('.bpx-player-ctrl-subtitle');
        const probe = window.__subtitleProbe;
        if (btn) {
            probe.button_found = true;
            btn.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true}));
            probe.click_dispatched = true;
            console.log('[SUB] Subtitle btn clicked');
        }
    """)


def _select_subtitle_language(driver):
    return driver.execute_script("""
        const items = Array.from(document.querySelectorAll('.bpx-player-ctrl-subtitle-language-item-text'));
        if (items && items.length > 0) {
            const target = items[0];
            console.log('[SUB] Clicking:', target.textContent.trim());
            target.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true}));
        } else {
            console.log('[SUB] No language items');
        }
    """)


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
    print(f"  如需更改，可删除或修改：{CONFIG_PATH}")
    print("=" * 50)
    return path


class SubtitleExtractor:
    def __init__(self, output_dir: Path = None, cookies_path: str = None,
                 compile_db_path: str = None, enrich_with_meta: bool = True,
                 browser: str = "chrome", close_browser: bool = False,
                 reuse_browser: bool = False,
                 backend: str = None,
                 asr_fallback: bool = False,
                 asr_model: str = "small",
                 min_delay: float = 8, max_delay: float = 15,
                 rate_limit_threshold: int = 2,
                 cooldown_seconds: int = 900):
        # 🆕 优先级：显式参数 > 配置文件 > SUBTITLE_DIR
        if output_dir is None:
            config = _load_config()
            saved_dir = config.get("output_dir")
            output_dir = Path(saved_dir) if saved_dir else SUBTITLE_DIR
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.cookies_path = cookies_path
        # 本项目 wiki DB 路径（compile_db.json，video-wiki-compile 维护）
        self.compile_db_path = Path(compile_db_path) if compile_db_path else COMPILE_DB_PATH
        # 🆕 v1.2 字幕保存后是否自动拼元数据 frontmatter（默认 True；--no-meta 关闭）
        self.enrich_with_meta = enrich_with_meta
        # 🆕 浏览器选择（默认 chrome；支持 edge / chrome）
        self.browser = browser.lower()
        self.backend = (backend or os.environ.get("BILIBILI_BROWSER_BACKEND", "selenium")).lower()
        self.asr_fallback = asr_fallback
        self.asr_model = asr_model
        self.close_browser = close_browser
        self.reuse_browser = reuse_browser
        self._driver = None
        self._playwright_context = None
        self.browser_start_count = 0
        self.browser_restart_count = 0
        if self.browser not in SUPPORTED_BROWSERS:
            raise ValueError(f"不支持的浏览器: {browser}，仅支持: {SUPPORTED_BROWSERS}")

        self.rate_limiter = RateLimiter(min_delay, max_delay)
        self.breaker = CircuitBreaker(
            self.output_dir / ".bili_guard_state.json",
            threshold=rate_limit_threshold,
            cooldown_seconds=cooldown_seconds,
        )
        self.last_failure_kind = None
        self.last_failure_message = ""
        self.last_probe = normalize_subtitle_probe()
        self.last_duration_sec = 0.0
        self._started_at = None

        # 下载列表（去重机制）
        self._download_list = None
        self._init_download_list()

    def _close_driver(self):
        driver, self._driver = self._driver, None
        if driver:
            try:
                driver.quit()
            except Exception as close_error:
                print(f"  [警告] 关闭浏览器失败: {close_error}")

    def close(self):
        """关闭共享浏览器；重复调用安全。"""
        self._close_driver()

    async def _extract_single_playwright_async(self, bvid: str) -> Optional[str]:
        profile = os.environ.get("BILIBILI_SFETCH_CHROME_PROFILE")
        profile_dir = Path(profile) if profile else self.output_dir.parent.parent / ".chrome-bilibili"
        from backends.playwright_subtitle import extract
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            result = await extract(
                f"https://www.bilibili.com/video/{bvid}", profile_dir,
                p.chromium.executable_path, PAGE_LOAD_TIMEOUT_SECONDS,
            )
        if result.status != "success":
            raise RuntimeError(result.error or result.status)
        output_path = self.output_dir / f"{bvid}.md"
        if not self.save_srt(result.body, str(output_path)):
            return None
        if self.enrich_with_meta:
            # Playwright backend closes its page before returning; use the
            # bundled metadata extractor as a best-effort second request.
            self._enrich_with_meta(output_path, bvid)
        self._update_db_subtitle_path(bvid, str(output_path))
        self._update_download_list(bvid, "success", str(output_path), duration_sec=round(time.monotonic() - self._started_at, 3))
        self.breaker.reset()
        return str(output_path)

    def _extract_single_playwright(self, bvid: str) -> Optional[str]:
        import asyncio
        return asyncio.run(self._extract_single_playwright_async(bvid))

    def _start_driver(self):
        browser_process_map = {"edge": "msedge.exe", "chrome": "chrome.exe"}
        if self.close_browser:
            proc_name = browser_process_map[self.browser]
            print(f"  [浏览器] 关闭 {self.browser}...")
            subprocess.run(f'taskkill /F /IM {proc_name} 2>nul', shell=True)
            time.sleep(2)

        from selenium import webdriver
        from selenium.webdriver.chrome.service import Service as ChromeService
        self.browser_start_count += 1
        if self.browser == "edge":
            from selenium.webdriver.edge.options import Options
            user_data_dir = edge_profile_dir()
            options = Options()
            options.add_argument(f"--user-data-dir={user_data_dir}")
            options.add_argument("--profile-directory=Default")
            print("  [浏览器] 启动 Edge...")
            return webdriver.Edge(options=options)
        from selenium.webdriver.chrome.options import Options
        user_data_dir = chrome_profile_dir()
        options = Options()
        chrome_binary = os.environ.get("BILIBILI_CHROME_BINARY")
        if chrome_binary and Path(chrome_binary).exists():
            options.binary_location = chrome_binary
        if os.environ.get("BILIBILI_HEADLESS") == "1":
            options.add_argument("--headless=new")
            options.add_argument("--disable-gpu")
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
        options.add_argument(f"--user-data-dir={user_data_dir}")
        options.add_argument("--profile-directory=Default")
        print("  [浏览器] 启动 Chrome...")
        driver_path = os.environ.get("BILIBILI_CHROMEDRIVER")
        if driver_path and Path(driver_path).exists():
            return webdriver.Chrome(service=ChromeService(driver_path), options=options)
        return webdriver.Chrome(options=options)

    # ─── 下载列表方法 ───────────────────────────────────────────────

    def _init_download_list(self):
        """初始化下载列表：已存在则加载，不存在则扫描 output_dir 现有文件后创建"""
        list_path = self.output_dir / DOWNLOAD_LIST_FILENAME
        if list_path.exists():
            self._download_list = self._load_download_list(list_path)
        else:
            self._download_list = self._create_download_list()
            self._scan_existing_files()
            self._save_download_list(list_path)

    def _get_download_list_path(self) -> Path:
        return self.output_dir / DOWNLOAD_LIST_FILENAME

    def _create_download_list(self) -> dict:
        """创建空的下载列表结构"""
        return {
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "records": {}
        }

    def _load_download_list(self, list_path: Path) -> dict:
        """从文件加载下载列表"""
        try:
            with open(list_path, encoding="utf-8") as f:
                data = json.load(f)
            print(f"  [下载列表] 已加载 {len(data.get('records', {}))} 条记录: {list_path.name}")
            return data
        except Exception as e:
            print(f"  [下载列表] 加载失败: {e}，将重新创建")
            return self._create_download_list()

    def _save_download_list(self, list_path: Path):
        """保存下载列表到文件"""
        self._download_list["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        try:
            with open(list_path, "w", encoding="utf-8") as f:
                json.dump(self._download_list, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"  [下载列表] 保存失败: {e}")

    def _scan_existing_files(self):
        """扫描 output_dir 下所有 .md 文件，提取 BV 号，生成初始记录"""
        print(f"  [下载列表] 扫描现有文件: {self.output_dir}")
        count = 0
        for f in self.output_dir.glob("*.md"):
            # 从文件名提取 BV 号
            name = f.stem  # e.g. "BV11RffBdEEQ"
            if name.startswith("BV"):
                self._download_list["records"][name] = {
                    "status": "success",
                    "downloaded_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "subtitle_path": str(f),
                    "source": "scan_existing"
                }
                count += 1
        if count > 0:
            print(f"  [下载列表] 从磁盘扫描到 {count} 个已有字幕文件")

    def _is_already_success(self, bvid: str) -> bool:
        """检查 BV 号是否已在列表中标记为 success"""
        record = self._download_list.get("records", {}).get(bvid)
        return record is not None and record.get("status") == "success"

    def _update_download_list(self, bvid: str, status: str, subtitle_path: str = None,
                              error: str = None, error_code: str = None,
                              retry_after=None, probe=None, duration_sec=0.0,
                              source: str = None, asr_model: str = None):
        """更新下载列表中某条记录"""
        record = {
            "status": status,
            "downloaded_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "schema_version": 2,
            "probe": normalize_subtitle_probe(probe),
            "duration_sec": duration_sec,
        }
        if subtitle_path:
            record["subtitle_path"] = subtitle_path
        if error:
            record["error"] = error
        if error_code:
            record["error_code"] = error_code
        if retry_after:
            record["retry_after"] = retry_after
        if source:
            record["source"] = source
        if asr_model:
            record["asr_model"] = asr_model

        with download_list_lock():
            list_path = self._get_download_list_path()
            if list_path.exists():
                self._download_list = self._load_download_list(list_path)
            self._download_list.setdefault("records", {})[bvid] = record
            self._save_download_list(list_path)

    def _record_failure(self, bvid: str, kind: str, message: str):
        self.last_failure_kind = kind
        self.last_failure_message = message
        self.last_duration_sec = round(
            time.monotonic() - self._started_at, 3
        ) if self._started_at is not None else 0.0
        self.breaker.record(kind)
        retry_after = self.breaker.retry_after
        if retry_after is not None:
            retry_after = retry_after.isoformat().replace("+00:00", "Z")
        status = "paused" if kind in (
            FailureKind.LOGIN_REQUIRED,
            FailureKind.RATE_LIMITED,
            FailureKind.BROWSER_START_FAILED,
        ) else "failed"
        self._update_download_list(
            bvid,
            status,
            error=message,
            error_code=kind,
            retry_after=retry_after,
            probe=self.last_probe,
            duration_sec=self.last_duration_sec,
        )

    def _run_asr_fallback(self, bvid: str) -> Optional[str]:
        """Transcribe audio only after native subtitle extraction confirmed NO_SUBTITLE."""
        from backends.asr_rescue import run
        result = run(
            bvid,
            self.output_dir.parent.parent / "tmp" / "audio",
            self.output_dir / f"{bvid}.md",
            browser=self.browser,
            model_size=self.asr_model,
        )
        if result.status != "success":
            raise RuntimeError(result.error or "ASR rescue failed")
        output_path = result.path
        if self.enrich_with_meta:
            self._enrich_with_meta(output_path, bvid)
        self._update_db_subtitle_path(bvid, str(output_path))
        self.last_duration_sec = round(time.monotonic() - self._started_at, 3)
        self._update_download_list(
            bvid, "success", str(output_path), probe=self.last_probe,
            duration_sec=self.last_duration_sec, source="asr",
            asr_model=result.model,
        )
        self.breaker.reset()
        self.last_failure_kind = None
        self.last_failure_message = ""
        return str(output_path)

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

    def extract_single(self, bvid: str, retry: bool = True) -> Optional[str]:
        """Run one guarded job; `retry` is retained for caller compatibility."""
        self.last_failure_kind = None
        self.last_failure_message = ""
        self.last_probe = normalize_subtitle_probe()
        self.last_duration_sec = 0.0
        if self._is_already_success(bvid):
            self._started_at = time.monotonic()
            print(f"  [跳过] {bvid} - 下载列表中已存在")
            self.last_duration_sec = round(time.monotonic() - self._started_at, 3)
            return "SKIPPED"
        self._started_at = time.monotonic()
        if not self.breaker.allow():
            kind = FailureKind.RATE_LIMITED
            message = "circuit open"
            self.last_failure_kind = kind
            self.last_failure_message = message
            self.last_duration_sec = round(time.monotonic() - self._started_at, 3)
            retry_after = self.breaker.retry_after
            retry_after = (
                retry_after.isoformat().replace("+00:00", "Z")
                if retry_after else None
            )
            self._update_download_list(
                bvid, "paused", error=message, error_code=kind,
                retry_after=retry_after, probe=self.last_probe,
                duration_sec=self.last_duration_sec,
            )
            return None

        self.rate_limiter.wait()
        for attempt in range(2):
            result = self._extract_single_once(bvid)
            if result is not None:
                return result
            if self.last_failure_kind not in (
                FailureKind.BROWSER_START_FAILED,
                FailureKind.PAGE_LOAD_FAILED,
            ) or attempt:
                return None
            print("  [重试] 浏览器/页面启动失败，最多重试一次...")
            self._close_driver()
            self.browser_restart_count += 1
            time.sleep(3)
        return None

    def _extract_single_once(self, bvid: str) -> Optional[str]:
        """提取单个视频的AI字幕

        Returns:
            字幕文件路径，失败返回 None
        """
        # 检查下载列表：已成功下载则跳过
        if self._is_already_success(bvid):
            print(f"  [跳过] {bvid} - 下载列表中已存在")
            self.last_duration_sec = round(
                time.monotonic() - self._started_at, 3
            ) if self._started_at is not None else 0.0
            return "SKIPPED"

        print(f"  [提取] {bvid}")
        if self.backend == "playwright":
            try:
                return self._extract_single_playwright(bvid)
            except Exception as e:
                print(f"  [错误] Playwright 提取失败: {e}")
                kind = FailureKind.NO_SUBTITLE if "未捕获到 AI 字幕" in str(e) else classify_failure(error=e)
                self._record_failure(bvid, kind, str(e))
                if kind == FailureKind.NO_SUBTITLE and self.asr_fallback:
                    try:
                        print(f"  [ASR] 原生字幕不存在，启动救援转录（模型: {self.asr_model}）")
                        return self._run_asr_fallback(bvid)
                    except Exception as rescue_error:
                        self._record_failure(bvid, FailureKind.UNKNOWN, f"ASR rescue failed: {rescue_error}")
                        print(f"  [错误] ASR 救援失败: {rescue_error}")
                return None
        video_url = f"https://www.bilibili.com/video/{bvid}"
        self.last_failure_kind = None
        self.last_failure_message = ""

        driver = self._driver if self.reuse_browser else None
        try:
            if driver is None:
                driver = self._start_driver()
                if self.reuse_browser:
                    self._driver = driver

            print(f"  [页面] 打开: {video_url}")
            if hasattr(driver, "set_page_load_timeout"):
                driver.set_page_load_timeout(PAGE_LOAD_TIMEOUT_SECONDS)
            try:
                driver.get(video_url)
            except Exception as error:
                from selenium.common.exceptions import TimeoutException
                if not isinstance(error, TimeoutException):
                    raise
                print("  [警告] 页面加载超时，继续处理已加载内容")

            print("  [等待] 播放器加载 (8s)...")
            time.sleep(8)

            session = check_login_state(driver)
            if not session.ok:
                print(f"  [停止] {session.reason}")
                if session.reason == FailureKind.PAGE_LOAD_FAILED:
                    self._close_driver()
                self._record_failure(bvid, session.reason, session.reason)
                return None

            # 注入 JS Hook
            print("  [注入] Extension-style JS Hook...")
            install_subtitle_probe(driver)

            # 点击字幕按钮
            print("  [操作] 点击字幕按钮...")
            try:
                _click_subtitle_button(driver)
                time.sleep(2)
            except Exception as e:
                print(f"  [错误] 点击字幕按钮: {e}")

            # 选择第一个语言项
            print("  [操作] 选择字幕语言...")
            try:
                _select_subtitle_language(driver)
                time.sleep(5)
            except Exception as e:
                print(f"  [错误] 选择语言: {e}")

            # 检查捕获的数据
            print("  [检查] 捕获字幕数据...")
            data = driver.execute_script("return window.__capturedSubtitleData")
            self.last_probe = read_subtitle_probe(driver)

            if data and data.get("body"):
                body = data["body"]
                output_path = self.output_dir / f"{bvid}.md"
                if self.save_srt(body, str(output_path)):
                    # 更新数据库
                    self._update_db_subtitle_path(bvid, str(output_path))
                    # 🆕 v1.2 拼元数据 frontmatter（best-effort，不阻塞主流程）
                    if self.enrich_with_meta:
                        self._enrich_with_meta(output_path, bvid, driver)
                    # 更新下载列表
                    self.last_duration_sec = round(time.monotonic() - self._started_at, 3)
                    self._update_download_list(
                        bvid, "success", str(output_path), probe=self.last_probe,
                        duration_sec=self.last_duration_sec,
                    )
                    self.breaker.reset()
                    self.last_failure_kind = None
                    self.last_failure_message = ""
                    return str(output_path)
            else:
                print(f"  [警告] 未捕获到字幕 (data={bool(data)})")
                kind = classify_failure(
                    response_status=self.last_probe.get("response_status"),
                    subtitle_probe=self.last_probe,
                )
                self._record_failure(bvid, kind, "未捕获到字幕")

            return None

        except Exception as e:
            print(f"  [错误] 提取失败: {e}")
            if driver:
                try:
                    self.last_probe = read_subtitle_probe(driver)
                except Exception:
                    pass
            kind = classify_failure(
                error=e,
                response_status=self.last_probe.get("response_status"),
                subtitle_probe=self.last_probe,
            )
            if kind in (FailureKind.BROWSER_START_FAILED, FailureKind.PAGE_LOAD_FAILED):
                self._close_driver()
            self._record_failure(bvid, kind, str(e))
            return None

        finally:
            if driver and not self.reuse_browser:
                try:
                    driver.quit()
                except Exception as close_error:
                    print(f"  [警告] 关闭浏览器失败: {close_error}")

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

    def _enrich_with_meta(self, subtitle_path: Path, bvid: str, driver=None) -> None:
        """🆕 v1.3 调用 extract_meta 抓取元数据并拼到字幕顶端

        优先从当前 Selenium 页面读取浏览器运行时元数据并直接拼 frontmatter；
        无浏览器 driver 时回退到 subprocess 调用独立脚本。

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

        if driver is not None and bundled_extract.exists():
            try:
                from extract_meta import extract_meta_from_state
                from prepend_meta import prepend_meta

                print(f"  [meta] 从当前浏览器运行时提取 → {bvid}")
                state = driver.execute_script("return window.__INITIAL_STATE__")
                meta_obj = extract_meta_from_state(bvid, state)
                if not isinstance(meta_obj, dict) or meta_obj.get("error"):
                    print("  [meta] 浏览器页面未返回有效元数据，跳过")
                    return
                if prepend_meta(meta_obj, subtitle_path):
                    print(f"  [meta] 已拼入 frontmatter → {subtitle_path.name}")
                return
            except Exception as e:
                print(f"  [meta] 浏览器页面提取失败，跳过: {e}")
                return

        try:
            print(f"  [meta] 抓取元数据 → {bvid}")
            meta_proc = subprocess.run(
                [sys.executable, str(extract_meta_script), bvid, "--indent", "0"],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
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
            if meta_obj.get("error"):
                print(f"  [meta] 抓取失败: {meta_obj['error']}")
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
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10,
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
            results.append({
                'bv_id': bv_id,
                'title': entry.get('title', ''),
                'subtitle_path': subtitle_path,
                'success': subtitle_path not in (None, "SKIPPED"),
            })
            if subtitle_path is None and self.last_failure_kind in (
                FailureKind.LOGIN_REQUIRED,
                FailureKind.RATE_LIMITED,
                FailureKind.BROWSER_START_FAILED,
            ):
                break

        return results

    def extract_favorites(self, favorites_file: str = None) -> list:
        """提取收藏夹所有视频的字幕

        Args:
            favorites_file: 收藏夹JSON文件路径
        """
        if favorites_file is None:
            favorites_file = favorites_path()

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
            results.append({
                'bv_id': bv_id,
                'title': title,
                'subtitle_path': subtitle_path,
                'success': subtitle_path not in (None, "SKIPPED"),
            })
            if subtitle_path is None and self.last_failure_kind in (
                FailureKind.LOGIN_REQUIRED,
                FailureKind.RATE_LIMITED,
                FailureKind.BROWSER_START_FAILED,
            ):
                break

        return results


def main():
    parser = argparse.ArgumentParser(description='B站AI字幕提取工具')
    parser.add_argument('bvid', nargs='?', help='BV号或视频URL（单视频模式）')
    parser.add_argument('--space', metavar='URL', help='UP主空间URL（空间模式）')
    parser.add_argument('--favorites', action='store_true', help='收藏夹模式')
    parser.add_argument('--output', '-o', metavar='DIR', default=None, help='输出目录')
    parser.add_argument('--no-meta', action='store_true', help='🆕 跳过字幕顶端拼元数据 frontmatter')
    parser.add_argument('--close-browser', action='store_true', help='启动前关闭同类浏览器进程（会影响其他窗口）')
    parser.add_argument('--browser', default='chrome', choices=['chrome', 'edge'],
                        help='浏览器类型（默认 chrome）')
    parser.add_argument('--backend', default='playwright', choices=['playwright', 'selenium'],
                        help='浏览器自动化后端（默认 playwright）')
    parser.add_argument('--asr-fallback', action='store_true',
                        help='原生字幕确认不存在时，下载音频并使用 faster-whisper 转录')
    parser.add_argument('--asr-model', default='small',
                        help='ASR 模型（默认 small；可选 tiny/base/medium/large-v3）')
    parser.add_argument('--min-delay', type=float, default=8, help='视频间最小间隔秒数')
    parser.add_argument('--max-delay', type=float, default=15, help='视频间最大间隔秒数')
    parser.add_argument('--rate-limit-threshold', type=int, default=2, help='连续限流次数后暂停')
    parser.add_argument('--cooldown-seconds', type=int, default=900, help='限流暂停秒数')
    parser.add_argument('--reset-config', action='store_true', help='🆕 重置配置文件，重新引导首次配置')

    args = parser.parse_args()
    if args.min_delay < 0 or args.max_delay < args.min_delay:
        parser.error('必须满足 0 <= min-delay <= max-delay')
    if args.rate_limit_threshold < 1 or args.cooldown_seconds < 0:
        parser.error('rate-limit-threshold 必须 >= 1，cooldown-seconds 必须 >= 0')

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
        backend=args.backend,
        asr_fallback=args.asr_fallback,
        asr_model=args.asr_model,
        close_browser=args.close_browser,
        min_delay=args.min_delay,
        max_delay=args.max_delay,
        rate_limit_threshold=args.rate_limit_threshold,
        cooldown_seconds=args.cooldown_seconds,
    )
    pause_exit_codes = {
        FailureKind.RATE_LIMITED: 10,
        FailureKind.LOGIN_REQUIRED: 11,
        FailureKind.BROWSER_START_FAILED: 12,
    }

    try:
        # 模式判断
        if args.favorites:
            print("=" * 50)
            print("模式: 收藏夹字幕")
            print("=" * 50)
            results = extractor.extract_favorites()
            success = sum(1 for r in results if r['success'])
            print(f"\n完成: {success}/{len(results)} 成功")
            if extractor.last_failure_kind in pause_exit_codes:
                sys.exit(pause_exit_codes[extractor.last_failure_kind])

        elif args.space:
            print("=" * 50)
            print("模式: UP主空间字幕")
            print("=" * 50)
            results = extractor.extract_space(args.space)
            success = sum(1 for r in results if r['success'])
            print(f"\n完成: {success}/{len(results)} 成功")
            if extractor.last_failure_kind in pause_exit_codes:
                sys.exit(pause_exit_codes[extractor.last_failure_kind])

        elif args.bvid:
            print("=" * 50)
            print("模式: 单视频字幕")
            print("=" * 50)
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
                print("\n跳过: 下载列表中已存在")
                sys.exit(0)
            elif result:
                print(f"\n成功: {result}")
                sys.exit(0)
            else:
                print("\n失败: 未能提取字幕")
                sys.exit(pause_exit_codes.get(extractor.last_failure_kind, 1))

        else:
            parser.print_help()
            print("\n示例:")
            print("  python subtitle_extractor.py BV11RffBdEEQ")
            print("  python subtitle_extractor.py --space https://space.bilibili.com/3546663834618256/upload/video")
            print("  python subtitle_extractor.py --favorites")
            sys.exit(1)
    finally:
        extractor.close()


if __name__ == "__main__":
    main()
