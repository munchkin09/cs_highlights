from dataclasses import replace
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from cs_highlights import platforms
from cs_highlights.commands import Result, Runner
from cs_highlights.config import create_config, load_config
from cs_highlights.csdm import CSDM
from cs_highlights.environment import csdm_game_dir, prerequisites
from cs_highlights.errors import HighlightsError
from cs_highlights.storage import exclusive_lock, write_json
from helpers import config


class PlatformTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="cs platforms ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def test_csdm_settings_follow_upstream_platform_layout(self):
        with patch.object(Path, "home", return_value=self.root), patch.dict(os.environ, {"XDG_CONFIG_HOME": str(self.root / "xdg")}):
            for os_name, relative in (("Linux", "xdg/csdm/settings.json"),
                                      ("Darwin", ".csdm/settings.json"),
                                      ("Windows", ".csdm/settings.json")):
                with self.subTest(os=os_name), patch.object(platforms, "system", return_value=os_name):
                    self.assertEqual(platforms.csdm_settings_path(), self.root / relative)

    def test_mac_template_loads_without_game_and_is_never_overwritten(self):
        path = self.root / "config.toml"
        with patch.object(platforms, "system", return_value="Darwin"):
            create_config(path)
            c = load_config(path)
        self.assertIsNone(c.cs2_dir)
        original = path.read_bytes()
        with self.assertRaisesRegex(HighlightsError, "Ya existe"):
            create_config(path)
        self.assertEqual(path.read_bytes(), original)

    def test_windows_custom_game_executable_resolves_game_root(self):
        c = config(self.root)
        executable = c.cs2_dir / "game/bin/win64/cs2.exe"
        executable.parent.mkdir(parents=True)
        executable.touch()
        settings = {"playback": {"customCs2LocationEnabled": True, "cs2ExecutablePath": str(executable)}}
        with patch.object(platforms, "system", return_value="Windows"):
            self.assertEqual(csdm_game_dir(settings), c.cs2_dir)
            self.assertEqual(platforms.cs2_executable(c.cs2_dir), executable)

    def test_steam_library_detection_works_with_windows_library_layout(self):
        steam = self.root / "Steam"
        (steam / "steamapps").mkdir(parents=True)
        library = self.root / "Games on another disk"
        escaped = str(library).replace("\\", "\\\\")
        (steam / "steamapps/libraryfolders.vdf").write_text(
            f'"libraryfolders" {{ "1" {{ "path" "{escaped}" "apps" {{ "730" "1" }} }} }}')
        with patch.object(platforms, "steam_roots", return_value=[steam]):
            self.assertEqual(csdm_game_dir({}), (library / "steamapps/common/Counter-Strike Global Offensive").resolve())

    def test_windows_process_checks_match_exact_names(self):
        runner = Mock()
        runner.run.return_value = Result(0, '"steam.exe","123","Console","1","10 K"\n'
                                           '"cs2-helper.exe","456","Console","1","10 K"\n')
        with patch.object(platforms, "system", return_value="Windows"):
            self.assertTrue(platforms.process_running("steam", runner))
            self.assertFalse(platforms.process_running("cs2", runner))
            runner.run.return_value = Result(0, '"CS2.EXE","987","Console","1","10 K"\n')
            self.assertTrue(platforms.process_running("cs2", runner))
            runner.run.return_value = Result(1, "")
            with self.assertRaises(HighlightsError):
                platforms.process_running("cs2", runner)

    def test_process_inspection_errors_are_not_treated_as_closed_game(self):
        runner = Mock()
        with patch.object(platforms, "system", return_value="Linux"):
            runner.run.return_value = Result(2, "")
            with self.assertRaises(HighlightsError):
                platforms.process_running("cs2", runner)
        with patch.object(platforms, "system", return_value="Windows"):
            for output in ("", "not a CSV process list"):
                runner.run.return_value = Result(0, output)
                with self.assertRaises(HighlightsError):
                    platforms.process_running("cs2", runner)

    def test_mac_steam_process_name(self):
        runner = Mock()
        runner.run.return_value = Result(0, "123")
        with patch.object(platforms, "system", return_value="Darwin"):
            self.assertTrue(platforms.process_running("steam", runner))
        self.assertEqual(runner.run.call_args.args[0], ["pgrep", "-x", "steam_osx"])

    def test_windows_csdm_cmd_is_adapted_without_shell_interpretation(self):
        install = self.root / "CS Demo Manager"
        (install / "resources").mkdir(parents=True)
        wrapper = install / "csdm.cmd"
        wrapper.touch()
        (install / "cs-demo-manager.exe").touch()
        (install / "resources/app.asar").touch()
        demo = r'C:\demos\a & %USERNAME% !name! ^ text.dem'
        runner = Mock()
        c = replace(config(self.root), csdm=str(wrapper))
        with patch.object(platforms, "system", return_value="Windows"):
            CSDM(c, runner).run(["json", demo, "--output-folder", str(self.root / "export")])
        args, kwargs = runner.run.call_args
        self.assertEqual(args[0][:4], [str(install / "cs-demo-manager.exe"),
                                     str(install / "resources/app.asar/cli.js"), "json", demo])
        self.assertEqual(kwargs["env"]["ELECTRON_RUN_AS_NODE"], "1")
        self.assertNotIn("shell", kwargs)

    def test_runner_rejects_unadapted_windows_shell_scripts(self):
        with patch.object(platforms, "system", return_value="Windows"):
            with self.assertRaisesRegex(HighlightsError, "cmd.exe"):
                Runner().run(["untrusted.cmd", "a & whoami"])

    def test_csdm_child_can_find_configured_postgresql_outside_path(self):
        binary = self.root / "PostgreSQL/bin/psql"
        runner = Mock()
        c = replace(config(self.root), psql=str(binary))
        with (patch.object(platforms, "system", return_value="Darwin"),
              patch("cs_highlights.csdm.shutil.which", side_effect=lambda tool: str(binary) if tool == c.psql else None),
              patch.dict(os.environ, {"PATH": "original-path"})):
            CSDM(c, runner).run(["json", "match.dem"])
            self.assertEqual(os.environ["PATH"], "original-path")
        self.assertEqual(runner.run.call_args.kwargs["env"]["PATH"],
                         str(binary.parent) + os.pathsep + "original-path")

    def test_windows_timeout_terminates_only_owned_process_tree(self):
        process = Mock(pid=12345)
        process.wait.side_effect = [subprocess.TimeoutExpired("tool.exe", 1), None]
        process.poll.return_value = None
        with (patch.object(platforms, "system", return_value="Windows"),
              patch("cs_highlights.commands.subprocess.Popen", return_value=process) as popen,
              patch("cs_highlights.commands.subprocess.run") as kill):
            with self.assertRaisesRegex(HighlightsError, "Tiempo agotado"):
                Runner().run(["tool.exe"], timeout=1)
        self.assertEqual(popen.call_args.kwargs["creationflags"], 0x200)
        self.assertNotIn("start_new_session", popen.call_args.kwargs)
        self.assertEqual(kill.call_args.args[0], ["taskkill", "/PID", "12345", "/T", "/F"])
        process.kill.assert_called_once()

    def test_lock_excludes_another_interpreter_then_releases(self):
        path = self.root / "shared.lock"
        source = (
            "from pathlib import Path\nimport sys\n"
            "from cs_highlights.storage import exclusive_lock\n"
            "from cs_highlights.errors import HighlightsError\n"
            "try:\n with exclusive_lock(Path(sys.argv[1])): pass\n"
            "except HighlightsError:\n sys.exit(7)\n"
        )
        env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"))
        def child():
            return subprocess.run([sys.executable, "-c", source, str(path)],
                                  env=env, capture_output=True, timeout=10)
        with exclusive_lock(path):
            result = child()
            self.assertEqual(result.returncode, 7, result.stderr)
        self.assertEqual(child().returncode, 0)


class PrerequisiteModesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.c = replace(config(self.root), cs2_dir=None)
        self.settings = {"database": {"hostname": "localhost", "port": 5432, "username": "csdm",
                                      "password": "test-secret", "database": "csdm"}}
        write_json(self.c.csdm_settings, self.settings)
        self.runner = Mock()
        self.runner.run.side_effect = self.run_tool

    def run_tool(self, argv, **kwargs):
        if "--help" in argv:
            return Result(0, "--output-folder --output --focus-player --recording-output --ffmpeg-input-parameters --config-file")
        if "SHOW server_version_num" in argv:
            return Result(0, "170005\n")
        if argv[0] == "pgrep":
            self.assertEqual(argv[-1], "steam_osx")
            return Result(0, "123")
        if argv[0] == "tasklist":
            return Result(0, '"steam.exe","123","Console","1","10 K"\n')
        return Result(0, "version 1")

    def test_mac_analysis_needs_no_game_steam_ffmpeg_or_display(self):
        with (patch.object(platforms, "system", return_value="Darwin"),
              patch("cs_highlights.environment.shutil.which", side_effect=lambda name: name)):
            checks, _ = prerequisites(self.c, self.runner, mode="analysis")
        self.assertTrue(all(check["ok"] for check in checks), checks)
        self.assertNotIn("test-secret", repr(checks))
        tools = [call.args[0][0] for call in self.runner.run.call_args_list]
        self.assertNotIn("pgrep", tools)
        self.assertNotIn("ffmpeg", tools)
        self.assertNotIn("ffprobe", tools)

    def test_mac_capture_fails_before_external_tools(self):
        with patch.object(platforms, "system", return_value="Darwin"):
            checks, _ = prerequisites(self.c, self.runner)
        self.assertIn("CS2 no tiene versión nativa", next(c["detail"] for c in checks if not c["ok"]))
        self.runner.run.assert_not_called()

    def test_mac_download_checks_steam_but_does_not_need_game(self):
        with (patch.object(platforms, "system", return_value="Darwin"),
              patch("cs_highlights.environment.shutil.which", side_effect=lambda name: name)):
            checks, _ = prerequisites(self.c, self.runner, mode="download")
        self.assertTrue(all(check["ok"] for check in checks), checks)
        self.assertIn("Steam abierto", [c["name"] for c in checks])

    def test_windows_capture_uses_exe_and_windows_process_list(self):
        c = config(self.root)
        executable = c.cs2_dir / "game/bin/win64/cs2.exe"
        executable.parent.mkdir(parents=True)
        executable.touch()
        ffmpeg = c.csdm_settings.parent / "ffmpeg/bin/ffmpeg.exe"
        ffmpeg.parent.mkdir(parents=True)
        ffmpeg.touch()
        self.settings.update(playback={"customCs2LocationEnabled": True, "cs2ExecutablePath": str(executable)},
                             video={"ffmpegSettings": {"customLocationEnabled": False}})
        write_json(c.csdm_settings, self.settings)
        with (patch.object(platforms, "system", return_value="Windows"),
              patch.dict(os.environ, {"SESSIONNAME": "Console", "SSH_CONNECTION": ""}),
              patch("cs_highlights.environment.shutil.which", side_effect=lambda name: name)):
            checks, _ = prerequisites(c, self.runner)
        self.assertTrue(all(check["ok"] for check in checks), checks)


if __name__ == "__main__":
    unittest.main()
