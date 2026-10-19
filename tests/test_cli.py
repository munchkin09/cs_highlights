from contextlib import redirect_stderr, redirect_stdout
import io
import signal
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cs_highlights.cli import main
from cs_highlights.errors import HighlightsError
from helpers import PLAYER, config


class CLITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.c = config(self.root)
        self.path = self.root / "config.toml"
        self.path.write_text(f'[player]\nsteam_id="{PLAYER}"\n[paths]\ndata_dir="data"\ncs2_dir="cs2"\n')
        self.output = io.StringIO()

    def test_run_continues_after_match_failure_and_reports_failure(self):
        with (patch("cs_highlights.cli.prerequisites", return_value=([{"name": "test", "ok": True, "detail": "OK"}], {})),
              patch("cs_highlights.cli.CSDM", autospec=True),
              patch("cs_highlights.cli.discover_and_download", return_value=([Path("bad.dem"), Path("good.dem")], False)),
              patch("cs_highlights.cli.Pipeline") as pipeline,
              redirect_stdout(self.output), redirect_stderr(self.output)):
            pipeline.return_value.process.side_effect = [HighlightsError("corrupt demo"), Path("good.mp4")]
            code = main(["--config", str(self.path), "run"])
        self.assertEqual(code, 1)
        self.assertEqual(pipeline.return_value.process.call_count, 2)
        self.assertIn("corrupt demo", self.output.getvalue())

    def test_no_new_matches_succeeds(self):
        with (patch("cs_highlights.cli.prerequisites", return_value=([], {})),
              patch("cs_highlights.cli.CSDM", autospec=True),
              patch("cs_highlights.cli.Pipeline"),
              patch("cs_highlights.cli.discover_and_download", return_value=([], False)),
              redirect_stdout(self.output)):
            self.assertEqual(main(["--config", str(self.path), "run"]), 0)
        self.assertIn("No hay demos", self.output.getvalue())

    def test_failed_prerequisite_prevents_acquisition(self):
        with (patch("cs_highlights.cli.prerequisites", return_value=([{"name": "Steam", "ok": False, "detail": "offline"}], {})),
              patch("cs_highlights.cli.discover_and_download") as download,
              redirect_stdout(self.output)):
            code = main(["--config", str(self.path), "run"])
        self.assertEqual(code, 1)
        download.assert_not_called()

    def test_interrupt_returns_130(self):
        with (patch("cs_highlights.cli.prerequisites", return_value=([], {})),
              patch("cs_highlights.cli.CSDM", autospec=True), patch("cs_highlights.cli.Pipeline") as pipeline,
              redirect_stdout(self.output), redirect_stderr(self.output)):
            pipeline.return_value.process.side_effect = KeyboardInterrupt
            code = main(["--config", str(self.path), "process", "demo.dem"])
        self.assertEqual(code, 130)

    def test_missing_config_has_readable_error(self):
        with redirect_stderr(self.output):
            code = main(["--config", str(self.root / "missing.toml"), "doctor"])
        self.assertEqual(code, 1)
        self.assertIn("config.example.toml", self.output.getvalue())

    def test_sigterm_reason_is_reported(self):
        import os

        def terminated(*args):
            os.kill(os.getpid(), signal.SIGTERM)

        with (patch("cs_highlights.cli.prerequisites", side_effect=terminated),
              redirect_stderr(self.output)):
            code = main(["--config", str(self.path), "doctor"])
        self.assertEqual(code, 130)
        self.assertIn("SIGTERM", self.output.getvalue())
