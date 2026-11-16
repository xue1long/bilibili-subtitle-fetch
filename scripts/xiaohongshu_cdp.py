"""用普通方式启动的 Chrome 承载小红书页面，再通过 CDP 交给 Playwright 读取。

为什么不用 Playwright 直接开浏览器：

- Playwright 启动的 Chromium 一定带 `--enable-automation`，`navigator.webdriver`
  为 true，小红书的风控会直接把笔记页跳到 `/login`；
- Chrome 136+ 又禁止在默认 user-data-dir 上开远程调试端口，所以不能直接附着
  用户正在运行的浏览器。

可行组合：用**普通方式**启动 Chrome（只加 `--remote-debugging-port`，不加任何自动化
参数），再用 CDP 让 Playwright 附着上去。整个过程不伪造指纹、不改 UA、不处理
验证码。

实测结论（2026-11-13）：带有效 `xsec_token` 的链接**不需要登录态**，用全新的空
profile（零 cookie）即可读出完整笔记；真正被拦的是浏览器 automation 标记，以及被
高频请求或版本降级污染过的 profile。所以默认使用 `.chrome-xiaohongshu-clean`，
连续出现 `LOGIN_REQUIRED` 时换成另一个全新空目录，不要人工登录。
"""

import os
import socket
import subprocess
import time
from pathlib import Path


class ChromeCdpError(RuntimeError):
    pass


def chrome_executable() -> Path | None:
    for var in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        base = os.environ.get(var)
        if not base:
            continue
        candidate = Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe"
        if candidate.is_file():
            return candidate
    return None


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def launch_chrome(profile_dir: Path, port: int, headless: bool = True) -> subprocess.Popen | None:
    """以普通方式启动 Chrome，只开放远程调试端口，不带自动化参数。

    headless 优先：不开窗口，也更少触发登录跳转；失败时再用有头模式兜底。
    """
    executable = chrome_executable()
    if not executable:
        return None
    profile_dir.mkdir(parents=True, exist_ok=True)
    command = [
        str(executable),
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "about:blank",
    ]
    if headless:
        command.insert(1, "--headless=new")
    return subprocess.Popen(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def cdp_endpoint(port: int) -> str:
    return f"http://127.0.0.1:{port}"


def wait_for_cdp(port: int, timeout: float = 25.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket() as sock:
            sock.settimeout(1)
            try:
                sock.connect(("127.0.0.1", port))
                return True
            except OSError:
                time.sleep(0.5)
    return False


def stop_chrome(process: subprocess.Popen) -> None:
    process.terminate()
    try:
        process.wait(timeout=10)
    except Exception:
        process.kill()
