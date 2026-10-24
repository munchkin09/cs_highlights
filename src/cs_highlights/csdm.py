import json
import os
from pathlib import Path
import re
import struct
import shutil

from .commands import Runner
from .config import Config
from .errors import HighlightsError
from . import platforms


def json_result(output: str, operation: str) -> dict:
    try:
        result = json.loads(output)
        if not isinstance(result, dict):
            raise ValueError("expected an object")
        return result
    except ValueError as exc:
        raise HighlightsError(f"CS Demo Manager devolvió JSON inválido en {operation}. "
                              "Comprueba que tu CLI admite --json.") from exc


class CSDM:
    def __init__(self, config: Config, runner: Runner):
        self.config = config
        self.runner = runner
        self._modern = None

    def run(self, arguments: list[str], **kwargs):
        argv, env = platforms.csdm_invocation(self.config.csdm, arguments)
        env = dict(os.environ if env is None else env)
        # CSDM also launches psql itself; an explicit tool path must work off PATH.
        directories = []
        for tool in (self.config.psql, self.config.ffmpeg, self.config.ffprobe):
            resolved = shutil.which(tool)
            if resolved:
                directory = str(Path(resolved).resolve().parent)
                if directory not in directories:
                    directories.append(directory)
        env["PATH"] = os.pathsep.join([*directories, env.get("PATH", "")])
        kwargs["env"] = env
        return self.runner.run(argv, **kwargs)

    @property
    def modern(self) -> bool:
        if self._modern is None:
            output = self.run(["dl-valve", "--help"], timeout=20).stdout
            self._modern = "--json" in output
        return self._modern

    def assert_idle(self) -> None:
        if platforms.process_running("cs2", self.runner):
            raise HighlightsError("CS2 está abierto o no se puede comprobar su estado. Ciérralo antes de grabar.")
        # v3.20.1 renders in the CLI process; newer builds delegate to a daemon queue.
        if not self.modern:
            return
        result = self.run(["video", "list", "--json"], timeout=60)
        data = json_result(result.stdout, "video list")
        videos = data.get("videos")
        if not isinstance(videos, list) or any(not isinstance(v, dict) or "status" not in v for v in videos):
            raise HighlightsError("Respuesta de cola de CS Demo Manager incompatible.")
        if any(v["status"] not in ("success", "error") for v in videos):
            raise HighlightsError("Hay vídeos pendientes o activos en CS Demo Manager. Espera o resuelve su cola "
                                  "antes de continuar; no se ha enviado otra grabación.")

    def export(self, demo: Path, destination: Path, log: Path) -> Path:
        destination.mkdir(parents=True, exist_ok=True)
        self.run(["json", str(demo), "--output-folder", str(destination),
                         "--source", "valve"], log=log, timeout=self.config.timeout_seconds)
        files = list(destination.glob("*.json"))
        if len(files) != 1:
            raise HighlightsError(f"Se esperaba un único JSON exportado en {destination}; hay {len(files)}.")
        return files[0]

    def analyze_positions(self, demo: Path, log: Path) -> None:
        self.run(["analyze", str(demo), "--analyze-positions", "--force"],
                 log=log, timeout=self.config.timeout_seconds)

    def assert_review_capabilities(self) -> None:
        analyze_help = self.run(["analyze", "--help"], timeout=20).stdout
        video_help = self.run(["video", "--help"], timeout=20).stdout
        if "--analyze-positions" not in analyze_help or "--true-view" not in video_help:
            raise HighlightsError("Esta versión de CS Demo Manager no admite el análisis de posiciones y TrueView "
                                  "necesarios para el modo deaths. Actualiza CS Demo Manager.")

    def _database_env(self) -> dict:
        settings = json.loads(self.config.csdm_settings.read_text(encoding="utf-8"))
        database = settings["database"]
        if database.get("mode", "external") != "external":
            raise HighlightsError("El modo deaths requiere la base PostgreSQL externa de CS Demo Manager.")
        env = {key: value for key, value in os.environ.items() if not key.startswith("PG")}
        env.update(PGHOST=str(database["hostname"]), PGPORT=str(database["port"]),
                   PGUSER=str(database["username"]), PGPASSWORD=str(database["password"]),
                   PGDATABASE=str(database["database"]), PGCONNECT_TIMEOUT="5")
        return env

    def _query(self, sql: str) -> list[list[str]]:
        result = self.runner.run([self.config.psql, "-X", "-w", "-A", "-t", "-F", "|", "-c", sql],
                                 timeout=90, env=self._database_env())
        return [line.split("|") for line in result.stdout.splitlines() if line]

    def position_count(self, checksum: str) -> int:
        if not re.fullmatch(r"[0-9a-fA-F]{32,64}", checksum):
            raise HighlightsError("Checksum de demo inválido al consultar posiciones.")
        rows = self._query(f"SELECT count(*) FROM player_positions WHERE match_checksum = '{checksum}'")
        return int(rows[0][0]) if rows else 0

    def player_positions(self, checksum: str, start_tick: int, end_tick: int) -> list[dict]:
        if not re.fullmatch(r"[0-9a-fA-F]{32,64}", checksum) or not 0 <= start_tick < end_tick:
            raise HighlightsError("Límites inválidos al consultar las posiciones de la demo.")
        sql = ("SELECT round_number,tick,player_steam_id,is_alive,x,y,z,yaw,flash_duration_remaining "
               "FROM player_positions "
               f"WHERE match_checksum = '{checksum}' AND tick >= {start_tick} AND tick < {end_tick} "
               "ORDER BY tick,player_steam_id")
        columns = ("round_number", "tick", "player_steam_id", "is_alive",
                   "x", "y", "z", "yaw", "flash_duration_remaining")
        rows = self._query(sql)
        try:
            result = []
            for row in rows:
                if len(row) != len(columns):
                    raise ValueError("column count")
                item = dict(zip(columns, row))
                for key in ("round_number", "tick"):
                    item[key] = int(item[key])
                for key in ("x", "y", "z", "yaw", "flash_duration_remaining"):
                    item[key] = float(item[key])
                item["is_alive"] = item["is_alive"].casefold() in {"t", "true", "1"}
                result.append(item)
            return result
        except (ValueError, IndexError) as exc:
            raise HighlightsError(f"CS Demo Manager devolvió posiciones incompatibles: {exc}") from exc

    def map_metadata(self, map_name: str) -> dict | None:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", map_name):
            raise HighlightsError("Nombre de mapa incompatible con CS Demo Manager.")
        rows = self._query("SELECT position_x,position_y,threshold_z,scale FROM maps "
                           f"WHERE name = '{map_name}' AND game = 'CS2'")
        if not rows:
            return None
        try:
            x, y, threshold, scale = map(float, rows[0])
            if not all(map(lambda n: n == n and abs(n) < 1e9, (x, y, threshold, scale))) or scale <= 0:
                return None
            root = Path(shutil.which(self.config.csdm) or self.config.csdm).resolve().parent
            asset_root = Path("static/images/maps/cs2/radars")
            user_radar_root = self.config.csdm_settings.parent / "images/maps/cs2/radars"
            radar = self._read_map_asset(user_radar_root / f"{map_name}.png")
            lower = self._read_map_asset(user_radar_root / f"{map_name}_lower.png")
            for parent in (root, *root.parents):
                for candidate in (parent / asset_root,
                                  parent / "static/images/maps/cs2/radars",
                                  parent / "resources/app.asar.unpacked" / asset_root):
                    if radar is None:
                        radar = self._read_map_asset(candidate / f"{map_name}.png")
                        if radar is None:
                            radar = self._read_map_asset(parent / "resources/app.asar",
                                                         f"static/images/maps/cs2/radars/{map_name}.png")
                    if lower is None:
                        lower = self._read_map_asset(candidate / f"{map_name}_lower.png")
                        if lower is None:
                            lower = self._read_map_asset(parent / "resources/app.asar",
                                                         f"static/images/maps/cs2/radars/{map_name}_lower.png")
            if not self._valid_radar(radar) or (lower is not None and not self._valid_radar(lower)):
                return None
            radar_size = struct.unpack(">II", radar[16:24])[0]
            return {"position_x": x, "position_y": y, "threshold_z": threshold, "scale": scale,
                    "radar": radar, "lower_radar": lower, "radar_size": radar_size}
        except (ValueError, IndexError):
            raise HighlightsError(f"Metadatos de mapa incompatibles en CS Demo Manager: {map_name}.")

    @staticmethod
    def _valid_radar(data: bytes | None) -> bool:
        if data is None or len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n":
            return False
        width, height = struct.unpack(">II", data[16:24])
        return width > 0 and width == height

    @staticmethod
    def _read_map_asset(path: Path, member: str | None = None) -> bytes | None:
        try:
            if member is None:
                return path.read_bytes()
            with path.open("rb") as archive:
                if len(archive.read(8)) != 8:
                    return None
                archive.seek(0)
                _, header_size = struct.unpack("<II", archive.read(8))
                header = json.loads(archive.read(header_size))
                entry = header["files"]
                for part in member.split("/"):
                    entry = entry[part]
                if entry.get("unpacked"):
                    return (path.parent / "app.asar.unpacked" / member).read_bytes()
                offset = int(entry["offset"])
                size = int(entry["size"])
                archive.seek(8 + header_size + offset)
                return archive.read(size)
        except (OSError, ValueError, KeyError, TypeError, struct.error, json.JSONDecodeError):
            return None

    def render(self, demo: Path, interval, output: Path, log: Path, *, true_view: bool = False,
               focus_player: bool = True, record_audio: bool = True) -> None:
        c = self.config
        output.mkdir(parents=True, exist_ok=True)
        ffmpeg = shutil.which(c.ffmpeg)
        if not ffmpeg:
            raise HighlightsError(f"No se encuentra FFmpeg: {c.ffmpeg}")
        self.run([
            "video", str(demo), str(interval.start_tick), str(interval.end_tick),
            "--output", str(output), "--recording-system", "CS",
            # Keep raw files in our private attempt folder until ffprobe validates the MP4.
            "--recording-output", "images-and-video", "--encoder-software", "FFmpeg",
            "--ffmpeg-executable-path", ffmpeg,
            "--width", str(c.width), "--height", str(c.height), "--framerate", str(c.fps),
            "--ffmpeg-video-codec", "libx264", "--ffmpeg-audio-codec", "aac",
            "--ffmpeg-video-container", "mp4", "--ffmpeg-audio-bitrate", "192",
            "--ffmpeg-input-parameters", "",
            f"--ffmpeg-output-parameters=-pix_fmt yuv420p -crf {c.crf} -movflags +faststart",
            "--no-player-voices", "--no-show-x-ray",
            "--show-assists", "--no-show-only-death-notices", "--death-notices-duration", "5",
            "--close-game-after-recording", "--no-concatenate-sequences",
        ] + (["--focus-player", interval.player_steam_id, "--cfg", "spec_mode 4"]
             if focus_player else [])
        + (["--record-audio"] if record_audio else ["--no-record-audio"])
        + (["--true-view"] if true_view else ["--no-true-view"]),
            log=log, timeout=c.timeout_seconds)

    def render_batch(self, demo: Path, captures: list, output: Path, log: Path) -> None:
        """Record independent sequences in one CS2 process, then encode after it exits."""
        c = self.config
        if not captures or len({item.true_view for item in captures}) != 1:
            raise HighlightsError("Lote de captura vacío o con opciones TrueView incompatibles.")
        ffmpeg = shutil.which(c.ffmpeg)
        if not ffmpeg:
            raise HighlightsError(f"No se encuentra FFmpeg: {c.ffmpeg}")
        sequences = []
        for number, item in enumerate(captures, 1):
            interval = item.interval
            sequences.append({
                "number": number, "startTick": interval.start_tick, "endTick": interval.end_tick,
                "showXRay": False, "showAssists": True, "showOnlyDeathNotices": False,
                "playersOptions": [], "cameras": [], "recordAudio": True,
                "playerVoicesEnabled": False, "deathNoticesDuration": 5,
                "playerCameras": [{"tick": interval.start_tick,
                                   "playerSteamId": interval.player_steam_id,
                                   "playerName": interval.player_steam_id}] if item.focus_player else [],
                "cfg": "spec_mode 4" if item.focus_player else "",
            })
        output.mkdir(parents=True, exist_ok=True)
        settings = {
            "demoPath": str(demo.resolve()), "outputFolderPath": str(output.resolve()),
            "recordingSystem": "CS", "recordingOutput": "images-and-video", "encoderSoftware": "FFmpeg",
            "width": c.width, "height": c.height, "framerate": c.fps,
            # CSDM's CLI awaits process exit before encoding. Close only after the last sequence.
            "closeGameAfterRecording": True, "concatenateSequences": False,
            "trueView": captures[0].true_view, "sequences": sequences,
            "ffmpegSettings": {
                "customExecutableLocation": ffmpeg, "audioBitrate": 192, "constantRateFactor": c.crf,
                "videoCodec": "libx264", "audioCodec": "aac", "videoContainer": "mp4",
                "inputParameters": "",
                "outputParameters": f"-pix_fmt yuv420p -crf {c.crf} -movflags +faststart",
            },
        }
        config_file = output / "recording.json"
        # The 3.20.1 CLI strips JSONC comments even inside strings; escape their delimiters in paths.
        config_file.write_text(json.dumps(settings, ensure_ascii=True, indent=2)
                               .replace("#", "\\u0023").replace("/", "\\u002f"), encoding="utf-8")
        self.run(["video", "--config-file", str(config_file.resolve())],
                 log=log, timeout=c.timeout_seconds)
