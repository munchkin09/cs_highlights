from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cs_highlights.config import load_config
from cs_highlights.environment import assert_raw_dirs_empty, csdm_game_dir, parse_vdf
from cs_highlights.errors import HighlightsError
from cs_highlights.storage import exclusive_lock, read_json, write_json
from cs_highlights.video import check_space
from helpers import PLAYER, config


class ConfigEnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.path = self.root / "config.toml"
        self.base = f'[player]\nsteam_id = "{PLAYER}"\n[paths]\ndata_dir = "data"\ncs2_dir = "cs2"\n'

    def test_paths_relative_to_config_not_cwd(self):
        self.path.write_text(self.base)
        c = load_config(self.path)
        self.assertEqual(c.data_dir, self.root / "data")
        self.assertEqual(c.width, 1920)
        self.assertNotEqual(c.fingerprint, replace(c, before_seconds=7).fingerprint)
        self.assertEqual(c.fingerprint, replace(c, data_dir=Path("/tmp/another")).fingerprint)

    def test_bad_values_rejected(self):
        for addition in ('[video]\nfps = 0', '[video]\nwidth = 801', '[video]\nfps = true',
                         '[selection]\nbefore_seconds = nan', '[selection]\nafter_seconds = -1',
                         '[runtime]\nreserve_gib = inf', '[unknown]\nx = 1'):
            self.path.write_text(self.base + addition)
            with self.subTest(addition=addition), self.assertRaises(HighlightsError):
                load_config(self.path)

    def test_missing_or_numeric_steam_id_rejected(self):
        self.path.write_text(self.base.replace(f'"{PLAYER}"', PLAYER))
        with self.assertRaises(HighlightsError):
            load_config(self.path)

    def test_existing_recordings_never_deleted(self):
        c = config(self.root)
        raw = c.raw_dirs[0]
        raw.mkdir(parents=True)
        recording = raw / "personal.wav"
        recording.write_text("keep")
        with self.assertRaisesRegex(HighlightsError, "No se han borrado"):
            assert_raw_dirs_empty(c)
        self.assertEqual(recording.read_text(), "keep")

    def test_symlink_scratch_rejected(self):
        c = config(self.root)
        c.raw_dirs[0].symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(HighlightsError):
            assert_raw_dirs_empty(c)

    def test_disk_check_includes_game_volume(self):
        c = config(self.root)
        c.data_dir.mkdir()
        with patch("cs_highlights.video.shutil.disk_usage") as usage:
            usage.return_value.free = 100
            with self.assertRaisesRegex(HighlightsError, "Espacio insuficiente"):
                check_space(c, c.data_dir, 8)

    def test_vdf_libraries(self):
        content = '"libraryfolders" { "0" { "path" "/games/Steam Library" "apps" { "730" "123" } } }'
        self.assertEqual(parse_vdf(content)["libraryfolders"]["0"]["path"], "/games/Steam Library")

    def test_custom_game_path_matches_csdm(self):
        c = config(self.root)
        executable = c.cs2_dir / "game/cs2.sh"
        executable.write_text("#!/bin/sh")
        settings = {"playback": {"customCs2LocationEnabled": True, "cs2ExecutablePath": str(executable)}}
        self.assertEqual(csdm_game_dir(settings), c.cs2_dir)

    def test_atomic_state_and_exclusive_lock(self):
        path = self.root / "state.json"
        write_json(path, {"status": "pending"})
        write_json(path, {"status": "done"})
        self.assertEqual(read_json(path), {"status": "done"})
        with exclusive_lock(self.root / ".lock"):
            with self.assertRaises(HighlightsError):
                with exclusive_lock(self.root / ".lock"):
                    pass
        with exclusive_lock(self.root / ".lock"):
            pass
