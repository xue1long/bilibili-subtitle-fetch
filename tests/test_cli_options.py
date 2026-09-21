import subprocess
import sys
import unittest
from pathlib import Path


class UnifiedCliOptionsTest(unittest.TestCase):
    def test_retry_and_status_options_are_documented(self):
        root = Path(__file__).parents[1]
        result = subprocess.run(
            [sys.executable, str(root / "scripts/cli.py"), "--help"],
            cwd=root, text=True, encoding="utf-8", capture_output=True, timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--retry-failed", result.stdout)
        self.assertIn("--only-status", result.stdout)


if __name__ == "__main__":
    unittest.main()
