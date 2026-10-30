"""Prepare the id list and url map the Xiaohongshu collector needs.

The share URLs recorded in the local note list carry an ``xsec_token`` that
expires, so they are only usable with a signed-in session — but they are still
the right entry point, because a bare ``/explore/<id>`` URL never renders.

Writes:
    tmp/xhs_ids.txt   one note id per line
    tmp/xhs_urls.json  {note_id: share url}
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from platform_meta import read_xiaohongshu_json
from runtime_paths import project_root

root = project_root()
notes = read_xiaohongshu_json(root / "10_raw" / "03_小红书收藏夹")

ids = sorted(notes)
urls = {vid: notes[vid]["source_url"] for vid in ids
        if notes[vid].get("source_url")}

tmp = root / "tmp"
tmp.mkdir(exist_ok=True)
(tmp / "xhs_ids.txt").write_text("\n".join(ids) + "\n", encoding="utf-8")
(tmp / "xhs_urls.json").write_text(json.dumps(urls, ensure_ascii=False, indent=1),
                                    encoding="utf-8")
print(f"notes {len(ids)} | with url {len(urls)}")
print("-> tmp/xhs_ids.txt, tmp/xhs_urls.json")