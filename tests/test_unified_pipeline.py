import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import sys

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

import cli
from sources.models import Video


class FakeExtractor:
    def __init__(self, **kwargs):
        self.output_dir = Path(kwargs["output_dir"]) if kwargs.get("output_dir") else Path(".")
        self._download_list = {"records": {}}
        self.last_failure_kind = None
        self.last_failure_message = ""

    def extract_single(self, bvid):
        return f"{bvid}.md"

    def close(self):
        pass


class UnifiedPipelineTest(unittest.TestCase):
    def run_with_source(self, source_name, videos):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(cli, "SubtitleExtractor", FakeExtractor), \
                    patch.object(cli, "project_root", return_value=Path(directory)), \
                    patch.object(cli, "discover_" + source_name, return_value=videos):
                args = ["--" + {"single": "video", "favorite": "favorite-url", "space": "space-url"}[source_name], "input", "--output", directory, "--min-delay", "0", "--max-delay", "0"]
                self.assertEqual(cli.main(args), 0)

    def test_single_video_pipeline(self):
        self.run_with_source("single", [Video("BV1234567890", source_type="single")])

    def test_favorite_pipeline(self):
        self.run_with_source("favorite", [Video("BV1234567890", source_type="favorite")])

    def test_space_pipeline(self):
        self.run_with_source("space", [Video("BV1234567890", source_type="space")])


if __name__ == "__main__":
    unittest.main()
