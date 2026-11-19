"""Agent-friendly lark-cli authorization helpers.

Interactive ``lark-cli auth login`` blocks forever in an agent session.
The device-code flow splits it into two calls:

1. ``auth login --no-wait --json --domain base,drive`` returns a
   ``verification_url`` and ``device_code`` without blocking.
2. Show the URL (and optionally a QR image) to the user; once they approve
   it, ``auth login --device-code <code>`` completes the login.

The domain is deliberately narrowed to ``base,drive``. ``--recommend`` or
``all`` pulls 100+ scopes, most of which this project never uses.

Examples::

    python scripts/feishu_auth.py start --qr tmp/feishu-login.png
    python scripts/feishu_auth.py complete <device_code>
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

DEFAULT_CLI = "lark-cli"
DEFAULT_DOMAIN = "base,drive"


def _run_cli(argv: list[str]) -> str:
    result = subprocess.run(argv, capture_output=True, text=True,
                            encoding="utf-8", errors="replace")
    if result.returncode != 0 and not result.stdout.strip():
        raise RuntimeError(
            f"lark-cli failed (rc={result.returncode}): "
            f"{(result.stderr or '').strip()[:300]}")
    return result.stdout


def start_device_auth(*, cli: str = DEFAULT_CLI,
                      domain: str = DEFAULT_DOMAIN,
                      runner=None) -> dict:
    """Start device login; return the URL and code without blocking."""
    argv = [cli, "auth", "login", "--no-wait", "--json",
            "--domain", domain]
    raw = (runner or _run_cli)(argv)
    payload = json.loads(raw)
    data = payload.get("data") if isinstance(payload, dict) else None
    if isinstance(data, dict):
        payload = data
    url = payload.get("verification_url") or payload.get("verificationUri") or ""
    code = payload.get("device_code") or payload.get("deviceCode") or ""
    if not url or not code:
        raise ValueError("device auth response lacks verification_url/device_code")
    return {"verification_url": url, "device_code": code,
            "domain": domain, "raw": payload}


def complete_device_auth(device_code: str, *, cli: str = DEFAULT_CLI,
                         runner=None) -> dict:
    """Exchange an approved device code for a logged-in session."""
    if not device_code or not device_code.strip():
        raise ValueError("device_code is required")
    argv = [cli, "auth", "login", "--device-code", device_code.strip()]
    raw = (runner or _run_cli)(argv)
    payload = json.loads(raw)
    if isinstance(payload, dict) and payload.get("ok") is False:
        raise RuntimeError("device code exchange failed: "
                           + json.dumps(payload.get("error"), ensure_ascii=False)[:300])
    return payload


def qrcode_png(url: str, dest: Path) -> Path | None:
    """Write a QR PNG when the optional ``qrcode`` package is available.

    Returns None instead of raising: the verification URL is always usable
    on its own, so a missing optional dependency must not block the flow.
    """
    try:
        import qrcode
    except ImportError:
        return None
    image = qrcode.make(url)
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    image.save(dest)
    return dest


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="飞书 agent 设备码授权")
    sub = parser.add_subparsers(dest="command", required=True)

    start = sub.add_parser("start", help="发起授权，拿到链接与设备码")
    start.add_argument("--domain", default=DEFAULT_DOMAIN)
    start.add_argument("--cli", default=DEFAULT_CLI)
    start.add_argument("--qr", type=Path, default=None,
                       help="可选：把验证链接生成二维码 PNG（需要 qrcode 包）")

    done = sub.add_parser("complete", help="用设备码完成登录")
    done.add_argument("device_code")
    done.add_argument("--cli", default=DEFAULT_CLI)

    args = parser.parse_args(argv)

    if args.command == "start":
        auth = start_device_auth(cli=args.cli, domain=args.domain)
        if args.qr:
            written = qrcode_png(auth["verification_url"], args.qr)
            if written:
                print(f"QR: {written}")
        print(f"URL: {auth['verification_url']}")
        print(f"DEVICE_CODE: {auth['device_code']}")
        print("用户确认后执行: "
              f"python scripts/feishu_auth.py complete {auth['device_code']}")
        return 0

    complete_device_auth(args.device_code, cli=args.cli)
    print("AUTH OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
