from dataclasses import replace
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from cs_highlights.capture import Capture, capture_many
from cs_highlights.csdm import CSDM
from cs_highlights.errors import HighlightsError
from cs_highlights.pipeline import Pipeline
from cs_highlights.storage import read_json, write_json
from helpers import ENEMY, PLAYER, config
from test_pipeline import FakeCSDM, probe_data


class BatchCaptureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.c = config(self.root)
        self.backend = FakeCSDM(self.c)
        self.messages = []
        self.pipeline = Pipeline(self.c, self.backend, report=self.messages.append)
        self.job = self.root / "job"
        self.job.mkdir()
        self.manifest = {"clips": {}, "attempts": {}}
        self.demo = self.root / "demo.dem"

    def captures(self, count, *, same_ticks=False):
        return [Capture(str(index), SimpleNamespace(
            start_tick=100 if same_ticks else 100 + index * 640,
            end_tick=612 if same_ticks else 612 + index * 640,
            player_steam_id=PLAYER if index % 2 == 0 else ENEMY),
            self.job / "clips" / f"{index:04d}.mp4", 8,
            {"player": PLAYER if index % 2 == 0 else ENEMY}) for index in range(count)]

    def run_capture(self, captures):
        capture_many(self.pipeline, self.demo, self.job, self.manifest,
                     lambda stage: write_json(self.job / "manifest.json", self.manifest), "render", captures)

    def test_multiple_clips_share_a_session_and_second_run_skips_every_capture(self):
        captures = self.captures(5)
        self.run_capture(captures)
        self.assertEqual(self.backend.batches, [["0", "1", "2", "3"]])
        self.assertEqual([len(batch["clips"]) for batch in self.manifest["capture_batches"]], [4, 1])
        self.assertTrue(all(batch["status"] == "complete" and batch["elapsed_seconds"] >= 0
                            for batch in self.manifest["capture_batches"]))
        self.assertEqual(list((self.job / "render").iterdir()), [])
        self.run_capture(captures)
        self.assertEqual(self.backend.renders, 5)

    def test_interrupted_batch_recovers_only_its_completed_numbered_sequence(self):
        captures = self.captures(4)
        self.backend.fail_at = 2
        with self.assertRaisesRegex(HighlightsError, "render failure"):
            self.run_capture(captures)
        self.assertEqual(self.manifest["capture_batches"][0]["status"], "interrupted")
        self.backend.fail_at = None
        self.run_capture(captures)
        self.assertEqual(self.backend.batches, [["0", "1", "2", "3"], ["1", "2", "3"]])
        self.assertEqual(self.backend.renders, 5)  # four usable clips and one failed attempt
        self.assertEqual(len(self.manifest["clips"]), 4)
        self.assertEqual(list((self.job / "render").iterdir()), [])

    def test_identical_ticks_for_different_players_remain_independent(self):
        captures = self.captures(2, same_ticks=True)
        self.run_capture(captures)
        self.assertEqual(self.manifest["clips"]["0"]["player"], PLAYER)
        self.assertEqual(self.manifest["clips"]["1"]["player"], ENEMY)
        self.assertTrue(all(item.path.exists() for item in captures))

    def test_ctrl_c_recovers_the_finished_clip_without_rendering_it_again(self):
        captures = self.captures(2)
        self.backend.interrupt_after_write = True
        with self.assertRaises(KeyboardInterrupt):
            self.run_capture(captures)
        self.run_capture(captures)
        self.assertEqual(self.backend.renders, 2)

    def test_low_disk_space_reduces_batch_size_without_changing_the_clips(self):
        def limited_space(c, job, duration):
            if duration > 12:
                raise HighlightsError("Espacio insuficiente")
        with patch("cs_highlights.capture.check_space", side_effect=limited_space):
            self.run_capture(self.captures(3))
        self.assertEqual(self.backend.batches, [])
        self.assertEqual([len(batch["clips"]) for batch in self.manifest["capture_batches"]], [1, 1, 1])
        self.assertTrue(any("espacio disponible" in message for message in self.messages))

    def test_insufficient_space_for_one_clip_never_starts_cs2(self):
        with patch("cs_highlights.capture.check_space", side_effect=HighlightsError("Espacio insuficiente")):
            with self.assertRaisesRegex(HighlightsError, "Espacio insuficiente"):
                self.run_capture(self.captures(2))
        self.assertEqual(self.backend.renders, 0)

    def test_legacy_attempts_remain_recoverable(self):
        captures = self.captures(1)
        attempt = self.job / "render/legacy/video-id"
        write_json(attempt / "old-sequence.mp4", probe_data(self.c, 8))
        self.manifest["attempts"]["0"] = ["render/legacy"]
        self.run_capture(captures)
        self.assertEqual(self.backend.renders, 0)
        self.assertTrue(captures[0].path.exists())

    def test_true_view_jobs_and_intro_use_separate_sessions(self):
        captures = self.captures(3)
        captures[1:] = [replace(item, true_view=True) for item in captures[1:]]
        self.run_capture(captures)
        self.assertEqual(self.backend.batches, [["1", "2"]])

    def test_setting_one_restores_a_session_per_clip(self):
        self.pipeline.config = replace(self.c, capture_batch_size=1)
        self.run_capture(self.captures(3))
        self.assertEqual(self.backend.batches, [])
        self.assertEqual(len(self.manifest["capture_batches"]), 3)


class CsdmBatchContractTests(unittest.TestCase):
    def test_config_file_controls_every_sequence_and_closes_cs2_after_the_batch(self):
        class RecordingRunner:
            def run(self, argv, **kwargs):
                self.argv = argv
                self.settings = read_json(Path(argv[argv.index("--config-file") + 1]))
                # Emulate the stable CLI's JSONC stripping before parsing its config.
                import re
                source = Path(argv[-1]).read_text()
                source = re.sub(r"#.*|//.*|/\*[\s\S]*?\*/", "", source)
                assert json.loads(source) == self.settings
                return SimpleNamespace(returncode=0, stdout="")
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            c = config(root)
            interval = SimpleNamespace(start_tick=100, end_tick=740, player_steam_id=PLAYER)
            captures = [Capture("intro", interval, root / "intro.mp4", 10, focus_player=False),
                        Capture("victim", interval, root / "victim.mp4", 10)]
            runner = RecordingRunner()
            with patch("cs_highlights.csdm.shutil.which", return_value="/usr/bin/ffmpeg"):
                CSDM(c, runner).render_batch(root / "demo #1.dem", captures,
                                           root / "capture #1", root / "capture.log")
            settings = runner.settings
            self.assertTrue(settings["closeGameAfterRecording"])
            self.assertFalse(settings["concatenateSequences"])
            self.assertEqual(settings["demoPath"], str((root / "demo #1.dem").resolve()))
            self.assertEqual((settings["width"], settings["height"], settings["framerate"]),
                             (c.width, c.height, c.fps))
            self.assertEqual(settings["ffmpegSettings"]["videoCodec"], "libx264")
            intro, victim = settings["sequences"]
            self.assertEqual(intro["playerCameras"], [])
            self.assertEqual(victim["playerCameras"][0]["playerSteamId"], PLAYER)
            self.assertEqual(victim["cfg"], "spec_mode 4")
            self.assertEqual([s["number"] for s in settings["sequences"]], [1, 2])
            self.assertTrue(all(not s["showXRay"] and not s["playerVoicesEnabled"]
                                and s["recordAudio"] for s in settings["sequences"]))
