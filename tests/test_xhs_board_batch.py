import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

import xhs_board_batch as batch


class BoardBatchTest(unittest.TestCase):
    def test_note_url_normalizes_board_listing(self):
        url = batch.note_url({"id": "6ab3c8c40000000014033363", "token": "TOKEN"})
        self.assertIn("/explore/6ab3c8c40000000014033363?", url)
        self.assertIn("xsec_token=TOKEN", url)
        self.assertIn("xsec_source=pc_user", url)
        self.assertNotIn("/board/", url)

    def test_discover_images_only_skips_video(self):
        video = lambda url, profile: ({"note_id": "v"}, "https://cdn/v.mp4", [])
        with self.assertRaises(batch.SkipNote):
            batch._discover_images_only("u", Path("p"), video)
        image = lambda url, profile: ({"note_id": "i"}, None, ["https://cdn/01.webp"])
        metadata, video_url, image_urls = batch._discover_images_only("u", Path("p"), image)
        self.assertIsNone(video_url)
        self.assertEqual(image_urls, ["https://cdn/01.webp"])

    def test_run_batch_resume_skip_video_and_report(self):
        notes = [
            {"id": "exists1", "token": "T1"},
            {"id": "image1", "token": "T2"},
            {"id": "video1", "token": "T3"},
            {"id": "broken1", "token": "T4"},
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "out"
            report = root / "report.json"
            done = output / "exists1"
            done.mkdir(parents=True)
            (done / "exists1.md").write_text("done", encoding="utf-8")

            calls = []

            def fake_run(url, output_dir, overwrite, model):
                calls.append(url)
                if "video1" in url:
                    raise batch.cli.XiaohongshuCliError(
                        "ASSET_DOWNLOAD_FAILED", "skip"
                    ) from batch.SkipNote("video note")
                if "broken1" in url:
                    raise RuntimeError("boom")
                images = output_dir / "image1" / "images"
                images.mkdir(parents=True, exist_ok=True)
                (images / "01.webp").write_bytes(b"image")
                return output_dir / "image1" / "image1.md"

            summary = batch.run_batch(notes, output, report, delay=0, runner=fake_run)

            self.assertEqual(
                summary,
                {"skipped_exists": 1, "ok": 1, "skipped_video": 1, "failed": 1},
            )
            rows = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual(
                [row["status"] for row in rows],
                ["skipped_exists", "ok", "skipped_video", "failed"],
            )
            self.assertEqual(rows[1]["images"], 1)
            self.assertEqual(rows[3]["code"], "RuntimeError")
            self.assertIn("/explore/image1?", calls[0])
            self.assertEqual(len(calls), 3)
            self.assertFalse(any("exists1" in url for url in calls))

    def test_main_uses_default_report_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            notes = root / "board_notes.json"
            notes.write_text(
                json.dumps([{"id": "a", "token": "T"}]), encoding="utf-8"
            )
            with patch.object(batch, "run_batch", return_value={"ok": 1}) as run:
                code = batch.main(
                    ["--notes", str(notes), "--output", str(root / "out")]
                )
            self.assertEqual(code, 0)
            self.assertEqual(run.call_args.args[2].name, "board_notes_report.json")


if __name__ == "__main__":
    unittest.main()
