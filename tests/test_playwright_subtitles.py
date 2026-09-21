import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

from subtitle_extractor import _subtitle_body_from_payload


def test_subtitle_body_parser_accepts_valid_ai_payload():
    payload = {"body": [{"from": 0.1, "to": 1.2, "content": "你好"}]}
    assert _subtitle_body_from_payload(payload) == payload["body"]


def test_subtitle_body_parser_rejects_empty_payload():
    assert _subtitle_body_from_payload({"body": []}) is None
