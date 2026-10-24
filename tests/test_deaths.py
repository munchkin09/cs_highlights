import tempfile
import unittest
import shutil
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from cs_highlights.analysis import normalize
from cs_highlights.csdm import CSDM
from cs_highlights.death_review import _freeze_time, _map_xy, _sample, render_map_frames
from cs_highlights.death_pipeline import _review_segments
from cs_highlights.deaths import DeathPlay, diagnose, select_deaths
from cs_highlights.commands import Runner
from cs_highlights.death_review import compose
from helpers import config
from helpers import ENEMY, PLAYER, raw_match
from cs_highlights.pipeline import Pipeline
from cs_highlights.storage import write_json
from cs_highlights.errors import HighlightsError
from test_pipeline import FakeCSDM, probe_data


class DeathBatchPipelineTests(unittest.TestCase):
    def test_both_perspectives_share_a_batch_and_composition_failure_never_rerecords(self):
        class ReviewCSDM(FakeCSDM):
            def assert_review_capabilities(self):
                pass

            def position_count(self, checksum):
                return 4

            def map_metadata(self, name):
                return {"radar": b"radar", "lower_radar": None, "radar_size": 64,
                        "position_x": 0, "position_y": 0, "scale": 1, "threshold_z": 0}

            def player_positions(self, checksum, start, end):
                return [{"round_number": 1, "tick": tick, "player_steam_id": player,
                         "is_alive": tick < 1200 or player == ENEMY,
                         "x": 0, "y": 0, "z": 0, "yaw": 0, "flash_duration_remaining": 0}
                        for tick in (start, 1199, 1200, end - 1) for player in (PLAYER, ENEMY)]

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            c = config(root)
            backend = ReviewCSDM(c)
            backend.raw = raw_match([{"tick": 1200, "roundNumber": 1, "killerSteamId": ENEMY,
                                      "victimSteamId": PLAYER, "killerSide": 2, "victimSide": 3,
                                      "weaponName": "ak47", "isHeadshot": False}])
            demo = root / "demo.dem"
            demo.write_bytes(b"PBDEMS2\0synthetic")
            pipeline = Pipeline(c, backend, report=lambda _: None)

            def frames(play, points, metadata, analysis, review, target, config):
                target.mkdir(parents=True, exist_ok=True)
                duration = (play.end_tick - play.start_tick) / analysis["tickrate"]
                return round(duration * config.fps), duration

            def composite(victim, killer, frames, output, duration, config, runner, **kwargs):
                write_json(output, probe_data(config, duration))

            with patch("cs_highlights.capture.replace_audio_with_silence"), \
                 patch("cs_highlights.death_pipeline.render_map_frames", side_effect=frames), \
                 patch("cs_highlights.death_pipeline.compose", side_effect=HighlightsError("composition failed")):
                with self.assertRaisesRegex(HighlightsError, "composition failed"):
                    pipeline.process(demo, mode="deaths")
            self.assertEqual(backend.renders, 5)
            self.assertEqual(backend.batches, [["0001-victim-s01", "0001-victim-s02",
                                               "0001-killer-s01", "0001-killer-s02"]])
            with patch("cs_highlights.capture.replace_audio_with_silence"), \
                 patch("cs_highlights.death_pipeline.render_map_frames", side_effect=frames), \
                 patch("cs_highlights.death_pipeline.compose", side_effect=composite):
                output = pipeline.process(demo, mode="deaths")
            self.assertTrue(output.is_file())
            self.assertEqual(backend.renders, 5)


class DeathSelectionTests(unittest.TestCase):
    def test_review_data_does_not_change_the_highlights_analysis_contract(self):
        analysis = normalize(raw_match())
        data = analysis.to_dict()
        self.assertNotIn("shots", data)
        self.assertNotIn("blinds", data)
        self.assertNotIn("intro_start_tick", data)
        self.assertEqual(analysis.intro_start_tick, 1)
        self.assertEqual(analysis.intro_end_tick, 100)
        self.assertNotIn("through_smoke", data["kills"][0])
        self.assertIn("shots", analysis.to_dict(include_review=True))

    def test_selects_only_enemy_deaths_and_keeps_each_death_separate(self):
        raw = raw_match([
            {"tick": 640, "roundNumber": 1, "killerSteamId": ENEMY, "victimSteamId": PLAYER,
             "killerSide": 2, "victimSide": 3, "weaponName": "ak47", "isHeadshot": True},
            {"tick": 640, "roundNumber": 1, "killerSteamId": ENEMY, "victimSteamId": PLAYER,
             "killerSide": 2, "victimSide": 3, "weaponName": "ak47", "isHeadshot": True},
            {"tick": 700, "roundNumber": 1, "killerSteamId": PLAYER, "victimSteamId": ENEMY,
             "killerSide": 3, "victimSide": 2, "weaponName": "ak47", "isHeadshot": True},
            {"tick": 800, "roundNumber": 1, "killerSteamId": ENEMY, "victimSteamId": PLAYER,
             "killerSide": 3, "victimSide": 3, "weaponName": "ak47", "isHeadshot": True},
        ])
        plays = select_deaths(normalize(raw), PLAYER, 10, 3)
        self.assertEqual(len(plays), 1)
        self.assertEqual((plays[0].start_tick, plays[0].end_tick, plays[0].death_tick), (100, 832, 640))
        self.assertEqual((plays[0].victim_steam_id, plays[0].killer_steam_id), (PLAYER, ENEMY))

    def test_round_boundary_and_zero_postroll_preserve_death_tick(self):
        raw = raw_match([{"tick": 100, "roundNumber": 1, "killerSteamId": ENEMY,
                          "victimSteamId": PLAYER, "killerSide": 2, "victimSide": 3,
                          "weaponName": "awp", "isHeadshot": True}])
        play, = select_deaths(normalize(raw), PLAYER, 10, 0)
        self.assertEqual((play.start_tick, play.end_tick), (100, 101))

    def test_diagnosis_keeps_observed_facts_separate_from_sound_hypothesis(self):
        raw = raw_match([{"tick": 640, "roundNumber": 1, "killerSteamId": ENEMY,
                          "victimSteamId": PLAYER, "killerSide": 2, "victimSide": 3,
                          "weaponName": "awp", "isHeadshot": True,
                          "isThroughSmoke": True, "penetratedObjects": 1}])
        raw["blinds"] = [{"tick": 620, "duration": 1, "flashedSteamId": PLAYER}]
        raw["shots"] = [{"tick": 610, "playerSteamId": PLAYER}]
        analysis = normalize(raw)
        play, = select_deaths(analysis, PLAYER, 10, 3)
        positions = [
            {"player_steam_id": PLAYER, "tick": 639, "is_alive": True, "x": 0, "y": 0, "yaw": 0},
            {"player_steam_id": ENEMY, "tick": 640, "is_alive": True, "x": -100, "y": 0, "yaw": 0},
        ]
        result = diagnose(play, analysis, positions)
        self.assertEqual({fact["rule"] for fact in result["facts"]},
                         {"kill_through_smoke", "penetrated_surface", "victim_blinded", "killer_behind_victim"})
        self.assertEqual(result["hypotheses"][0]["rule"], "own_shot_before_death")
        self.assertIn("audibility", result["not_evaluable"])

    def test_missing_optional_events_are_not_reported_as_negative_findings(self):
        raw = raw_match([{"tick": 640, "roundNumber": 1, "killerSteamId": ENEMY,
                          "victimSteamId": PLAYER, "killerSide": 2, "victimSide": 3,
                          "weaponName": "awp", "isHeadshot": True}])
        analysis = normalize(raw)
        play, = select_deaths(analysis, PLAYER, 10, 3)
        result = diagnose(play, analysis, [])
        self.assertEqual(result["facts"], [])
        self.assertEqual(result["hypotheses"], [])
        self.assertTrue({"kill_through_smoke", "penetrated_surface", "victim_blinded",
                         "own_shot_before_death", "relative_orientation_missing_positions"}
                        <= set(result["not_evaluable"]))


class MinimapTests(unittest.TestCase):
    def test_uses_csdm_radar_coordinates_and_rejects_large_position_gaps(self):
        metadata = {"position_x": -1000, "position_y": 2000, "scale": 2, "radar_size": 1024}
        self.assertEqual(_map_xy({"x": 0, "y": 1000}, metadata, 512), (250, 250))
        points = [{"tick": 10, "x": 1, "y": 2, "is_alive": True},
                  {"tick": 100, "x": 4, "y": 8, "is_alive": True}]
        self.assertIsNone(_sample(points, 50, 10))
        self.assertEqual(_sample(points, 10, 10)["x"], 1)

    def test_freezes_a_killer_who_died_before_the_review_interval(self):
        points = [{"tick": 100, "is_alive": False}, {"tick": 120, "is_alive": False}]
        self.assertEqual(_freeze_time(points, 100, 64, 60), 1 / 60)
        points = [{"tick": 100, "is_alive": True}, {"tick": 164, "is_alive": False}]
        self.assertEqual(_freeze_time(points, 100, 64, 60), 1.0)

    def test_review_capture_segments_are_contiguous_and_at_most_ten_seconds(self):
        segments = _review_segments(100, 100 + 13 * 64, 64)
        self.assertEqual(segments[0][0], 100)
        self.assertEqual(segments[-1][1], 100 + 13 * 64)
        self.assertTrue(all(left[1] == right[0] for left, right in zip(segments, segments[1:])))
        self.assertTrue(all(end - start <= 10 * 64 for start, end in segments))

    def test_opening_capture_leaves_the_demo_camera_unfocused(self):
        class CaptureRunner:
            def run(self, argv, **kwargs):
                self.argv = argv
                return SimpleNamespace(stdout="", stderr="", returncode=0)
        with tempfile.TemporaryDirectory() as folder:
            c = config(Path(folder))
            runner = CaptureRunner()
            csdm = CSDM(c, runner)
            interval = SimpleNamespace(start_tick=1, end_tick=100)
            with patch("cs_highlights.csdm.shutil.which", return_value="/usr/bin/ffmpeg"):
                csdm.render(Path("demo.dem"), interval, Path(folder) / "intro",
                            Path(folder) / "intro.log", focus_player=False)
            self.assertNotIn("--focus-player", runner.argv)
            self.assertNotIn("--cfg", runner.argv)
            self.assertIn("--record-audio", runner.argv)

    def test_renders_synchronized_map_and_evidence_frames(self):
        from PIL import Image
        import io
        radar = Image.new("RGB", (64, 64), (45, 60, 75))
        buffer = io.BytesIO()
        radar.save(buffer, format="PNG")
        play = DeathPlay(100, 180, 1, 160, PLAYER, ENEMY,
                         normalize(raw_match([{"tick": 160, "roundNumber": 1,
                                              "killerSteamId": ENEMY, "victimSteamId": PLAYER,
                                              "killerSide": 2, "victimSide": 3,
                                              "weaponName": "ak47", "isHeadshot": False}])).kills[0])
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder)
            count, duration = render_map_frames(
                play, [], {"radar": buffer.getvalue(), "lower_radar": None,
                           "radar_size": 64, "position_x": 0, "position_y": 0,
                           "scale": 1, "threshold_z": 0},
                {"tickrate": 64}, {"facts": [], "hypotheses": []}, target,
                type("Config", (), {"fps": 4, "width": 800, "height": 600})())
            self.assertEqual(count, 5)
            self.assertAlmostEqual(duration, 80 / 64)
            self.assertEqual(len(list(target.glob("*.png"))), count)
            with Image.open(target / "000000.png") as first_frame:
                self.assertEqual(first_frame.size, (800, 300))

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg/ffprobe no instalados")
    def test_composite_uses_two_cameras_and_the_killers_audio(self):
        from cs_highlights.video import validate_video
        from cs_highlights.video import replace_audio_with_silence
        from PIL import Image
        from dataclasses import replace
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            c = replace(config(root), fps=10)
            runner = Runner()
            victim, killer = root / "victim.mp4", root / "killer.mp4"
            for path, color, frequency in ((victim, "red", 440), (killer, "blue", 880)):
                runner.run([c.ffmpeg, "-nostdin", "-y", "-v", "error", "-f", "lavfi", "-i",
                            f"color=c={color}:s=800x600:r=10:d=1", "-f", "lavfi", "-i",
                            f"sine=frequency={frequency}:sample_rate=48000:duration=1", "-c:v", "libx264",
                            "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)])
            replace_audio_with_silence(victim, 1, c, runner)
            self.assertEqual(validate_video(victim, 1, c, runner)["audio_codec"], "aac")
            frames = root / "frames"
            frames.mkdir()
            for index in range(10):
                Image.new("RGB", (800, 300), (20, 30, 40)).save(frames / f"{index:06d}.png")
            output = root / "review.mp4"
            compose(victim, killer, frames, output, 1, c, runner, victim_freeze=0.4)
            result = validate_video(output, 1, c, runner)
            self.assertEqual((result["width"], result["height"], result["audio_codec"]),
                             (800, 600, "aac"))
