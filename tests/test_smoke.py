import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cs_highlights.storage import read_json
from helpers import config
from test_pipeline import FakeCSDM


class SmokeBatchTests(unittest.TestCase):
    def test_batch_smoke_joins_two_contiguous_clips_from_one_game_session(self):
        script = Path(__file__).resolve().parents[1] / "scripts/smoke_linux.py"
        spec = importlib.util.spec_from_file_location("smoke_linux", script)
        smoke = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(smoke)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            c = config(root)
            backend = FakeCSDM(c)
            demo = root / "demo.dem"
            demo.write_bytes(b"PBDEMS2\0synthetic")
            with patch.object(smoke, "load_config", return_value=c), \
                 patch.object(smoke, "Runner", return_value=backend.runner), \
                 patch.object(smoke, "CSDM", return_value=backend), \
                 patch.object(smoke, "prerequisites", return_value=([{"name": "fake", "ok": True,
                                                                          "detail": "test"}], {})), \
                 patch("sys.argv", [str(script), "--batch", str(demo)]), patch("builtins.print"):
                self.assertEqual(smoke.main(), 0)
            validation = read_json(next(c.data_dir.glob("smoke/*/validation.json")))
            self.assertEqual(backend.batches, [["1", "2"]])
            self.assertEqual(len(validation["capture_batches"]), 1)
            self.assertEqual(validation["capture_batches"][0]["status"], "complete")
            self.assertTrue(Path(validation["output"]).is_file())
            self.assertEqual(validation["visual_review"], "pending")
