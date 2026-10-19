from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

from cs_highlights.commands import Result
from cs_highlights.csdm import CSDM
from cs_highlights.errors import HighlightsError
from cs_highlights.pipeline import Pipeline, capture_segments
from cs_highlights.selection import Interval
from cs_highlights.storage import read_json, write_json
from helpers import config, raw_match


def probe_data(c, duration):
    return {"format": {"duration": str(duration), "format_name": "mov,mp4,m4a,3gp,3g2,mj2"}, "streams": [
        {"codec_type": "video", "codec_name": "h264", "pix_fmt": "yuv420p", "width": c.width,
         "height": c.height, "avg_frame_rate": f"{c.fps}/1", "duration": str(duration)},
        {"codec_type": "audio", "codec_name": "aac", "duration": str(duration)},
    ]}


class MediaRunner:
    """Fake only external media executables; pipeline state and files are real."""
    def __init__(self, c):
        self.config = c

    def run(self, argv, **kwargs):
        if argv[0] == self.config.ffprobe:
            return Result(0, Path(argv[-1]).read_text())
        if argv[0] == self.config.ffmpeg:
            playlist = Path(argv[argv.index("-i") + 1])
            clips = [playlist.parent / line.split("'")[1] for line in playlist.read_text().splitlines()]
            duration = sum(float(read_json(p)["format"]["duration"]) for p in clips)
            write_json(Path(argv[-1]), probe_data(self.config, duration))
            return Result(0, "")
        raise AssertionError(argv)


class FakeCSDM:
    def __init__(self, c):
        self.config = c
        self.runner = MediaRunner(c)
        self.raw = raw_match()
        self.exports = 0
        self.renders = 0
        self.fail_at = None
        self.interrupt_after_write = False
        self.busy = False

    def export(self, demo, destination, log):
        self.exports += 1
        destination.mkdir(parents=True)
        target = destination / "match.json"
        write_json(target, self.raw)
        return target

    def assert_idle(self):
        if self.busy:
            raise HighlightsError("cola activa")

    def render(self, demo, interval, output, log):
        self.renders += 1
        if self.renders == self.fail_at:
            raise HighlightsError("render failure")
        target = output / "video-id/1-sequence.mp4"
        duration = (interval.end_tick - interval.start_tick) / self.raw["tickrate"]
        write_json(target, probe_data(self.config, duration))
        (target.parent / "owned.tga").write_text("raw frame")
        if self.interrupt_after_write:
            self.interrupt_after_write = False
            raise KeyboardInterrupt


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.c = config(self.root)
        self.demo = self.root / "my match.dem"
        self.demo.write_bytes(b"PBDEMS2\0synthetic-test-header-not-a-real-demo")
        self.backend = FakeCSDM(self.c)
        self.pipeline = Pipeline(self.c, self.backend, {"test": True}, report=lambda _: None)

    def manifest(self):
        return read_json(next(self.c.data_dir.glob("matches/*/*/manifest.json")))

    def test_end_to_end_artifacts_and_second_run_skips_work(self):
        output = self.pipeline.process(self.demo)
        self.assertTrue(output.exists())
        self.assertEqual(self.backend.exports, 1)
        self.assertEqual(self.backend.renders, 4)
        self.assertEqual(self.manifest()["status"], "complete")
        self.assertTrue((output.parent / "analysis.json").exists())
        self.assertEqual(list(output.parent.rglob("*.tga")), [])
        self.assertEqual(self.pipeline.process(self.demo), output)
        self.assertEqual((self.backend.exports, self.backend.renders), (1, 4))

    def test_failed_render_resumes_after_validated_clip(self):
        self.backend.fail_at = 2
        with self.assertRaisesRegex(HighlightsError, "render failure"):
            self.pipeline.process(self.demo)
        self.assertEqual(self.manifest()["status"], "failed")
        self.assertEqual(len(self.manifest()["clips"]), 1)
        self.backend.fail_at = None
        output = self.pipeline.process(self.demo)
        self.assertTrue(output.exists())
        self.assertEqual(self.backend.exports, 1)
        self.assertEqual(self.backend.renders, 5)  # 1 valid + 1 failed + remaining 3

    def test_interrupted_after_recording_recovers_output_without_rerecording(self):
        self.backend.interrupt_after_write = True
        with self.assertRaises(KeyboardInterrupt):
            self.pipeline.process(self.demo)
        self.assertEqual(self.manifest()["status"], "interrupted")
        self.pipeline.process(self.demo)
        self.assertEqual(self.backend.renders, 4)

    def test_active_daemon_does_not_enqueue_duplicate(self):
        self.backend.busy = True
        with self.assertRaisesRegex(HighlightsError, "cola activa"):
            self.pipeline.process(self.demo)
        self.assertEqual(self.backend.renders, 0)

    def test_no_kills_is_success_without_video(self):
        self.backend.raw = raw_match([])
        self.assertIsNone(self.pipeline.process(self.demo))
        self.assertEqual(self.manifest()["status"], "no_highlights")
        self.assertEqual(self.backend.renders, 0)

    def test_invalid_analysis_does_not_poison_raw_cache(self):
        self.backend.raw = {"wrong": "schema"}
        with self.assertRaises(HighlightsError):
            self.pipeline.process(self.demo)
        self.assertEqual(list(self.c.data_dir.glob("matches/*/*/raw.json")), [])
        self.backend.raw = raw_match()
        self.pipeline.process(self.demo)
        self.assertEqual(self.backend.exports, 2)

    def test_corrupt_demo_rejected_before_external_processes(self):
        self.demo.write_bytes(b"corrupt")
        with self.assertRaisesRegex(HighlightsError, "demo CS2"):
            self.pipeline.process(self.demo)
        self.assertEqual(self.backend.exports, 0)

    def test_output_corruption_rebuilds_from_existing_clips(self):
        output = self.pipeline.process(self.demo)
        output.write_text("corrupt")
        self.pipeline.process(self.demo)
        self.assertEqual(self.backend.renders, 4)
        self.assertEqual(self.manifest()["status"], "complete")

    def test_new_settings_preserve_previous_output(self):
        first = self.pipeline.process(self.demo)
        changed = replace(self.c, before_seconds=2)
        second = Pipeline(changed, FakeCSDM(changed), report=lambda _: None).process(self.demo)
        self.assertNotEqual(first, second)
        self.assertTrue(first.exists())

    def test_long_won_clutch_without_kills_is_captured_and_joined(self):
        self.backend.raw = raw_match([])
        self.backend.raw['clutches'] = [{'tick': 3100, 'roundNumber': 2,
                                        'clutcherSteamId': self.c.steam_id,
                                        'opponentCount': 3, 'won': True}]
        output = self.pipeline.process(self.demo)
        manifest = self.manifest()
        self.assertTrue(output.exists())
        self.assertEqual(manifest['status'], 'complete')
        selection = read_json(output.parent / 'intervals.json')
        self.assertEqual(len(selection['intervals']), 1)
        self.assertGreater(len(selection['capture_segments']), 1)
        self.assertEqual(manifest['expected_duration'], (7000 - 3000) / 64)
        self.assertTrue(all(c['probe']['duration'] <= 10 for c in manifest['clips'].values()))

    def test_capture_segments_have_no_gaps_duplicates_or_tiny_tail(self):
        block = Interval(100, 1381, 1, self.c.steam_id, 'won_clutch', (100, 740, 1380))
        segments = capture_segments([block], 64)
        self.assertEqual(segments[0].start_tick, block.start_tick)
        self.assertEqual(segments[-1].end_tick, block.end_tick)
        self.assertTrue(all(a.end_tick == b.start_tick for a, b in zip(segments, segments[1:])))
        self.assertEqual(tuple(t for c in segments for t in c.kill_ticks), block.kill_ticks)
        self.assertTrue(all(320 <= c.end_tick - c.start_tick <= 640 for c in segments))


class QueueContractTests(unittest.TestCase):
    def test_pending_work_is_never_implicitly_resumed(self):
        class Busy:
            def run(self, argv, **kwargs):
                if argv[0] == "pgrep":
                    return Result(1, "")
                if "--help" in argv:
                    return Result(0, "--json")
                return Result(0, json.dumps({"isPaused": True, "videos": [{"status": "pending"}]}))
        with tempfile.TemporaryDirectory() as folder:
            c = config(Path(folder))
            with self.assertRaisesRegex(HighlightsError, "pendientes"):
                CSDM(c, Busy()).assert_idle()

    def test_legacy_does_not_call_nonexistent_queue_api(self):
        class Legacy:
            def run(self, argv, **kwargs):
                if argv[0] == "pgrep":
                    return Result(1, "")
                if "--help" in argv:
                    return Result(0, "--output")
                raise AssertionError(argv)
        with tempfile.TemporaryDirectory() as folder:
            CSDM(config(Path(folder)), Legacy()).assert_idle()
