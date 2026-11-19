import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

from feishu_auth import (  # noqa: E402
    complete_device_auth,
    qrcode_png,
    start_device_auth,
)


class DeviceAuthTest(unittest.TestCase):
    def test_start_uses_no_wait_json_and_narrow_domain(self):
        seen = []

        def runner(argv):
            seen.append(argv)
            return json.dumps({"ok": True, "data": {
                "verification_url": "https://example.test/verify",
                "device_code": "DC123",
            }})

        auth = start_device_auth(runner=runner)
        self.assertEqual(auth["verification_url"], "https://example.test/verify")
        self.assertEqual(auth["device_code"], "DC123")
        self.assertEqual(seen[0][:5], ["lark-cli", "auth", "login", "--no-wait", "--json"])
        self.assertEqual(seen[0][-2:], ["--domain", "base,drive"])

    def test_start_accepts_flat_payload_and_custom_domain(self):
        def runner(argv):
            return json.dumps({"verification_url": "https://x/v",
                               "device_code": "DC9"})

        auth = start_device_auth(domain="base", runner=runner)
        self.assertEqual(auth["domain"], "base")
        self.assertEqual(auth["device_code"], "DC9")

    def test_start_rejects_payload_without_code(self):
        with self.assertRaises(ValueError):
            start_device_auth(runner=lambda argv: json.dumps({"ok": True, "data": {}}))

    def test_complete_passes_device_code(self):
        seen = []

        def runner(argv):
            seen.append(argv)
            return json.dumps({"ok": True, "data": {}})

        result = complete_device_auth(" DC123 ", runner=runner)
        self.assertTrue(result["ok"])
        self.assertEqual(seen[0], ["lark-cli", "auth", "login",
                                   "--device-code", "DC123"])

    def test_complete_rejects_empty_code(self):
        with self.assertRaises(ValueError):
            complete_device_auth("  ", runner=lambda argv: "{}")

    def test_complete_raises_on_failure_payload(self):
        def runner(argv):
            return json.dumps({"ok": False, "error": {"code": 1}})
        with self.assertRaises(RuntimeError):
            complete_device_auth("DC1", runner=runner)


class QrCodeTest(unittest.TestCase):
    def test_missing_optional_package_returns_none(self):
        import builtins
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "qrcode":
                raise ImportError("not installed")
            return real_import(name, *args, **kwargs)

        builtins.__import__ = fake_import
        try:
            self.assertIsNone(qrcode_png("https://x/v", Path("unused.png")))
        finally:
            builtins.__import__ = real_import


if __name__ == "__main__":
    unittest.main()
