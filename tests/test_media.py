from dataclasses import replace
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

from cs_highlights.commands import Runner
from cs_highlights.errors import HighlightsError
from cs_highlights.video import concatenate, validate_video
from helpers import config


class RunnerTests(unittest.TestCase):
    def test_no_shell_and_stderr_is_separate_and_logged(self):
        with tempfile.TemporaryDirectory() as folder:
            log = Path(folder) / "run.log"
            text = "$(do-not-run); `do-not-run` spaces"
            result = Runner().run([sys.executable, "-c", "import sys; print(sys.argv[1]); print('error detail', file=sys.stderr)", text], log=log)
            self.assertEqual(result.stdout.strip(), text)
            self.assertEqual(result.stderr.strip(), "error detail")
            self.assertIn(text, log.read_text())

    def test_timeout_stops_owned_process(self):
        with tempfile.TemporaryDirectory() as folder:
            log = Path(folder) / "timeout.log"
            with self.assertRaisesRegex(HighlightsError, "Tiempo agotado"):
                Runner().run([sys.executable, "-c", "import time; print('recording started', flush=True); time.sleep(60)"],
                             timeout=1, log=log)
            self.assertIn("recording started", log.read_text())


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg/ffprobe no instalados")
class RealMediaTests(unittest.TestCase):
    def test_real_h264_aac_concat_and_validation(self):
        with tempfile.TemporaryDirectory(prefix="cs-highlights media ") as folder:
            root = Path(folder).resolve()
            c = config(root)
            clips = root / "clips"
            clips.mkdir()
            runner = Runner()
            paths = []
            for index, color in enumerate(("red", "blue"), 1):
                path = clips / f"{index:04d}.mp4"
                runner.run([c.ffmpeg, "-nostdin", "-y", "-v", "error", "-f", "lavfi", "-i",
                            f"color=c={color}:s=800x600:r=10:d=1", "-f", "lavfi", "-i",
                            "sine=frequency=440:sample_rate=48000:duration=1", "-c:v", "libx264",
                            "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)])
                validate_video(path, 1, c, runner)
                paths.append(path)
            output = root / "highlights.mp4"
            metadata = concatenate(paths, output, 2, c, runner)
            self.assertAlmostEqual(metadata["duration"], 2, delta=0.1)
            self.assertEqual(metadata["video_codec"], "h264")
            segments = root / "perspectives/segments"
            segments.mkdir(parents=True)
            parts = []
            for index, path in enumerate(paths, 1):
                part = segments / f"0001-victim-s{index:02d}.mp4"
                shutil.copyfile(path, part)
                parts.append(part)
            perspective = root / "perspectives/0001-victim.mp4"
            metadata = concatenate(parts, perspective, 2, c, runner, clip_directory="segments")
            self.assertAlmostEqual(metadata["duration"], 2, delta=0.1)
            self.assertIn("segments/0001-victim-s01.mp4", perspective.with_suffix(".concat.txt").read_text())
            with self.assertRaises(HighlightsError):
                validate_video(output, 10, c, runner)
            with self.assertRaises(HighlightsError):
                validate_video(output, 2, replace(c, width=1920), runner)
