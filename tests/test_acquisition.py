import json
from pathlib import Path
import tempfile
import unittest

from cs_highlights.acquisition import discover_and_download
from cs_highlights.commands import Result
from cs_highlights.csdm import CSDM
from cs_highlights.errors import HighlightsError
from helpers import config


class DownloadRunner:
    def __init__(self, modern=True, failure=False):
        self.modern = modern
        self.failure = failure
        self.downloads = 0

    def run(self, argv, **kwargs):
        if "--help" in argv:
            return Result(0, "--output --json" if self.modern else "--output")
        folder = Path(argv[argv.index("--output") + 1])
        demo = folder / "match.dem"
        skipped = demo.exists()
        if not skipped:
            demo.write_bytes(b"PBDEMS2\0example")
            demo.with_suffix(".dem.info").write_bytes(b"completed")
            self.downloads += 1
        if self.failure:
            (folder / "broken.dem").write_bytes(b"partial")
        summary = {"downloaded": [] if skipped else [{"demoPath": str(demo)}],
                   "skipped": [{"demoPath": str(demo)}] if skipped else [],
                   "failed": [{"error": "connection lost"}] if self.failure else []}
        return Result(1 if self.failure else 0, json.dumps(summary) if self.modern else "Downloading match 1/1...")


class AcquisitionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.c = config(Path(self.temp.name).resolve())

    def test_download_and_dedup_for_both_cli_generations(self):
        for modern in (True, False):
            with self.subTest(modern=modern):
                runner = DownloadRunner(modern)
                csdm = CSDM(self.c, runner)
                demos, failed = discover_and_download(csdm)
                self.assertFalse(failed)
                self.assertEqual(len(demos), 1)
                before = runner.downloads
                discover_and_download(csdm)
                self.assertEqual(runner.downloads, before)
                self.assertTrue(demos[0].with_suffix(".dem.info").exists())

    def test_partial_failure_publishes_only_completed_downloads(self):
        for modern in (True, False):
            with self.subTest(modern=modern):
                demos, failed = discover_and_download(CSDM(self.c, DownloadRunner(modern, failure=True)))
                self.assertTrue(failed)
                self.assertEqual([d.name for d in demos], ["match.dem"])
                self.assertFalse((self.c.data_dir / "downloads/broken.dem").exists())

    def test_interrupted_file_is_not_mistaken_for_completed_demo(self):
        staging = self.c.data_dir / "downloads/incoming"
        staging.mkdir(parents=True)
        (staging / "match.dem").write_text("incomplete")
        runner = DownloadRunner()
        demos, _ = discover_and_download(CSDM(self.c, runner))
        self.assertEqual(runner.downloads, 1)
        self.assertTrue(demos[0].read_bytes().startswith(b"PBDEMS2"))
        self.assertEqual(len(list(staging.parent.glob("interrupted-*"))), 1)

    def test_legacy_steam_failure_with_zero_exit_is_failure(self):
        class Offline(DownloadRunner):
            def run(self, argv, **kwargs):
                if "--help" in argv:
                    return Result(0, "--output")
                return Result(0, "Retrieving last MM matches...", "Steam is not running or not logged in.")
        demos, failed = discover_and_download(CSDM(self.c, Offline(False)))
        self.assertEqual(demos, [])
        self.assertTrue(failed)

    def test_invalid_modern_json_is_not_silent_success(self):
        class Broken(DownloadRunner):
            def run(self, argv, **kwargs):
                return Result(0, "--json" if "--help" in argv else "oops")
        with self.assertRaises(HighlightsError):
            discover_and_download(CSDM(self.c, Broken()))

    def test_legacy_no_matches_is_success(self):
        class Empty(DownloadRunner):
            def run(self, argv, **kwargs):
                return Result(0, "--output" if "--help" in argv else "No matches found.")
        demos, failed = discover_and_download(CSDM(self.c, Empty(False)))
        self.assertFalse(failed)
        self.assertEqual(demos, [])
