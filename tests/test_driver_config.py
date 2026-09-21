import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

import _driver_patch as driver_patch


class DriverConfigTest(unittest.TestCase):
    def test_defaults_do_not_contain_machine_specific_user_or_version(self):
        for value in (driver_patch.DEFAULT_CHROMEDRIVER, driver_patch.DEFAULT_CHROME_BIN):
            if value:
                self.assertNotIn("C:\\Users\\HP", value)
                self.assertNotIn("147.0.7727.117", value)


if __name__ == "__main__":
    unittest.main()
