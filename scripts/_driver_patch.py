"""Selenium Edge driver monkey-patch（共享模块）。

【为什么需要这个 patch】
B 站字幕 skill 的 subtitle_extractor.py 直接调用 webdriver.Edge()，
Selenium 4 的内置 SeleniumManager 会去下载 msedgedriver。
在公司/受限网络下，msedgedriver.azureedge.net CDN 经常被挡，
导致 skill 一启动就报 "Unable to obtain driver for MicrosoftEdge"。

本模块把 selenium.webdriver.Edge 替换成：
  - driver: 本地 chromedriver.exe（已有缓存即可，不需联网）
  - binary: Playwright 自带的 chromium.exe（同上）
  - options: skill 内部 import 出来的 EdgeOptions 强转 ChromeOptions
    （chromedriver 不认 ms:edgeOptions capability key，会报
    "No matching capabilities found"）

【典型用法】
  from _driver_patch import patch_selenium_edge
  patch_selenium_edge()           # 调一次，全局生效
  import subtitle_extractor        # 之后 import 就走 patch
  ext = subtitle_extractor.SubtitleExtractor(...)

【路径覆盖】
环境变量可覆盖默认路径：
  BILIBILI_SFETCH_CHROMEDRIVER  # 可选：本地 chromedriver.exe
  BILIBILI_SFETCH_CHROME_BIN     # 可选：本地 Chrome/Chromium 可执行文件
"""
import os
import shutil
from pathlib import Path

# Environment overrides are preferred; PATH discovery keeps the module portable.
DEFAULT_CHROMEDRIVER = shutil.which("chromedriver") or shutil.which("chromedriver.exe")
DEFAULT_CHROME_BIN = shutil.which("chrome") or shutil.which("chrome.exe")

CHROMEDRIVER_PATH = os.environ.get("BILIBILI_SFETCH_CHROMEDRIVER", DEFAULT_CHROMEDRIVER)
CHROME_BIN_PATH = os.environ.get("BILIBILI_SFETCH_CHROME_BIN", DEFAULT_CHROME_BIN)


def _check_paths():
    """打印路径警告；未配置时让 Selenium 使用自己的驱动管理。"""
    for label, p in [("chromedriver", CHROMEDRIVER_PATH), ("chrome", CHROME_BIN_PATH)]:
        if p and not Path(p).exists():
            print(
                f"[_driver_patch] 警告: {label} 路径不存在: {p}\n"
                f"  如需覆盖请设环境变量 BILIBILI_SFETCH_CHROMEDRIVER / BILIBILI_SFETCH_CHROME_BIN"
            )


def patch_selenium_edge():
    """替换 selenium.webdriver.Edge → 走本地 chromedriver + Playwright chromium。

    只有两条本地路径都可解析时才启用；否则保留 Selenium Manager 的默认行为。
    """
    if not CHROMEDRIVER_PATH or not CHROME_BIN_PATH:
        print("[_driver_patch] 未配置完整本地 driver，保留 Selenium Manager")
        return False

    import selenium.webdriver
    from selenium.webdriver.chrome.service import Service as ChromeService
    from selenium.webdriver.chrome.options import Options as ChromeOptions
    from selenium.webdriver.chromium.webdriver import ChromiumDriver

    def to_chrome_options(opts):
        co = ChromeOptions()
        if opts is None:
            return co
        for arg in getattr(opts, "arguments", []):
            co.add_argument(arg)
        for k, v in getattr(opts, "experimental_options", {}).items():
            co.add_experimental_option(k, v)
        return co

    def patched_edge(options=None, service=None, keep_alive=False):
        co = to_chrome_options(options)
        if CHROME_BIN_PATH:
            co.binary_location = CHROME_BIN_PATH
        if service is None and CHROMEDRIVER_PATH:
            service = ChromeService(CHROMEDRIVER_PATH)
        return ChromiumDriver(
            browser_name="chrome",
            vendor_prefix="goog",
            options=co,
            service=service,
            keep_alive=keep_alive,
        )

    selenium.webdriver.Edge = patched_edge
    _check_paths()
    return True


def get_skill_dir() -> Path:
    """返回本 skill scripts 目录的绝对路径。"""
    return Path(__file__).resolve().parent
