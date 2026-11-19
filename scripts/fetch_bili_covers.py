"""Fetch Bilibili cover URLs for a JSON list of records.

The retry/backoff policy lives in ``bili_guard``; this module only supplies
the HTTP transport and a small command line interface.

Usage::

    python scripts/fetch_bili_covers.py records.json covers.json --limit 10
"""
from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request
from pathlib import Path

from bili_guard import FetchPolicy, fetch_covers


VIEW_API = "https://api.bilibili.com/x/web-interface/view?bvid={bvid}"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def view_url(bvid: str) -> str:
    return VIEW_API.format(bvid=bvid)


def make_fetch(timeout: float = 20.0, opener=None):
    """Build a ``(status, payload, error)`` fetcher for one record."""
    open_url = opener or urllib.request.urlopen

    def fetch(record: dict):
        request = urllib.request.Request(view_url(record["bvid"]), headers={
            "User-Agent": USER_AGENT,
            "Referer": "https://www.bilibili.com/",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9",
        })
        try:
            with open_url(request, timeout=timeout) as response:
                body = response.read().decode("utf-8", errors="replace")
                return response.status, json.loads(body), None
        except urllib.error.HTTPError as exc:
            return exc.code, None, None
        except Exception as exc:
            return None, None, exc

    return fetch


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="JSON array of records with bvid")
    parser.add_argument("output", type=Path, help="where to write fetched covers")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--batch-gap", type=float, default=0.12,
                        help="seconds between first-pass batches")
    parser.add_argument("--rounds", type=int, default=4)
    args = parser.parse_args(argv)

    records = json.loads(args.input.read_text(encoding="utf-8"))
    if args.limit:
        records = records[:args.limit]
    policy = FetchPolicy(concurrency=args.concurrency,
                         batch_gap=args.batch_gap,
                         max_rounds=args.rounds)
    result = fetch_covers(records, make_fetch(), policy=policy)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result["records"], ensure_ascii=False, indent=1),
        encoding="utf-8")
    print(json.dumps({
        "input": len(records),
        "saved": len(result["records"]),
        "permanent": len(result["permanent"]),
        "pending": len(result["pending"]),
        "stats": result["stats"],
        "output": str(args.output),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
