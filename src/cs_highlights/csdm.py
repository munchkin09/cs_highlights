import json
import os
from pathlib import Path
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

    def render(self, demo: Path, interval, output: Path, log: Path) -> None:
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
            "--focus-player", interval.player_steam_id, "--cfg", "spec_mode 4",
            "--record-audio", "--no-player-voices", "--no-show-x-ray", "--no-true-view",
            "--show-assists", "--no-show-only-death-notices", "--death-notices-duration", "5",
            "--close-game-after-recording", "--no-concatenate-sequences",
        ], log=log, timeout=c.timeout_seconds)
