"""Prerequisites for analysis, downloads, and native CS2 capture."""
import os
from pathlib import Path
import platform
import re
import shutil
import tempfile

from .commands import Runner
from .config import Config
from .csdm import CSDM
from .errors import HighlightsError
from .storage import read_json
from . import platforms


def parse_vdf(content: str) -> dict:
    tokens = re.findall(r'"((?:\\.|[^"\\])*)"|([{}])', content)
    stream = iter(a.replace('\\\\', '\\').replace('\\"', '"') if not b else b for a, b in tokens)

    def block(nested=False):
        result = {}
        for key in stream:
            if key == "}":
                if not nested:
                    raise ValueError("unexpected closing brace")
                return result
            value = next(stream)
            result[key] = block(True) if value == "{" else value
        if nested:
            raise ValueError("missing closing brace")
        return result

    return block()


def csdm_game_dir(settings: dict) -> Path:
    playback = settings.get("playback", {})
    if playback.get("customCs2LocationEnabled"):
        executable = Path(playback.get("cs2ExecutablePath", "")).expanduser()
        return platforms.game_root_from_executable(executable)
    candidates = platforms.steam_roots()
    steam = next((p for p in candidates if p.exists()), candidates[-1])
    if "/snap/" in str(steam.resolve()) or "/.var/app/" in str(steam.resolve()):
        raise HighlightsError("La integración requiere Steam nativo; no admite Steam Flatpak ni Snap.")
    try:
        libraries = parse_vdf((steam / "steamapps/libraryfolders.vdf").read_text())["libraryfolders"]
        for item in libraries.values():
            if isinstance(item, dict) and "730" in item.get("apps", {}):
                return (Path(item["path"]) / "steamapps/common/Counter-Strike Global Offensive").resolve()
    except (OSError, KeyError, TypeError, ValueError, StopIteration) as exc:
        raise HighlightsError("No se puede resolver la instalación de CS2 usada por CS Demo Manager. "
                              "Configura su ubicación personalizada en Playback.") from exc
    raise HighlightsError("CS2 no aparece en las bibliotecas de Steam.")


def assert_raw_dirs_empty(config: Config) -> None:
    for path in config.raw_dirs:
        # CSDM recursively removes these folders at the start of a recording.
        if path.is_symlink() or (path.exists() and (not path.is_dir() or any(path.iterdir()))):
            raise HighlightsError(f"CS Demo Manager limpiaría archivos previos en {path}. "
                                  "Muévelos a otra carpeta antes de grabar. No se han borrado.")
        if path.resolve() != path.absolute():
            raise HighlightsError(f"La carpeta temporal atraviesa un enlace simbólico: {path}")


def prerequisites(config: Config, runner: Runner, *, mode: str = "capture") -> tuple[list[dict], dict]:
    if mode not in {"capture", "analysis", "download"}:
        raise ValueError(f"Unknown prerequisite mode: {mode}")
    checks = []
    versions = {"python": platform.python_version(), "platform": platform.platform(), "mode": mode}
    backend = CSDM(config, runner)
    capture = mode == "capture"

    def check(name, action):
        try:
            detail = action()
            checks.append({"name": name, "ok": True, "detail": str(detail or "OK")})
        except (HighlightsError, OSError, ValueError, KeyError, TypeError, AttributeError, IndexError) as exc:
            checks.append({"name": name, "ok": False, "detail": str(exc)})

    def require(condition, message):
        if not condition:
            raise HighlightsError(message)

    check("Sistema operativo", lambda: require(platforms.system() in {"Linux", "Windows", "Darwin"},
                                               "Se admiten Linux, Windows y macOS."))
    if capture:
        check("Captura CS2", lambda: require(platforms.supports_capture(),
              "CS2 no tiene versión nativa para macOS. Usa analyze o download; "
              "para grabar el MP4 se necesita Windows o Linux con CS2."))
        if not platforms.supports_capture():
            return checks, versions
        if platforms.system() == "Linux":
            check("Sesión gráfica", lambda: require(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"),
                                                   "Ejecuta la CLI desde tu escritorio Linux."))
        else:
            check("Sesión gráfica", lambda: require(os.environ.get("SESSIONNAME") != "Services"
                  and not os.environ.get("SSH_CONNECTION"), "Ejecuta la CLI desde tu escritorio Windows."))
    tools = ("csdm", "ffmpeg", "ffprobe", "psql") if capture else ("csdm", "psql")
    for name in tools:
        def executable(name=name):
            binary = getattr(config, name)
            require(shutil.which(binary), f"No se encuentra {binary}.")
            flag = "help" if name == "csdm" else "--version" if name == "psql" else "-version"
            result = backend.run([flag], timeout=15) if name == "csdm" else runner.run([binary, flag], timeout=15)
            version = result.stdout.strip().splitlines()[0]
            versions[name] = version
            return version
        check(name, executable)

    def cli_contract():
        commands = [(["json"], ["--output-folder"])]
        if mode != "analysis":
            commands.append((["dl-valve"], ["--output"]))
        if capture:
            commands.append((["video"], ["--focus-player", "--recording-output", "--ffmpeg-input-parameters"]))
            if config.capture_batch_size > 1:
                commands[-1][1].append("--config-file")
        for command, options in commands:
            output = backend.run([*command, "--help"], timeout=20).stdout
            require(all(option in output for option in options),
                    "La versión de CS Demo Manager no ofrece las opciones de CLI requeridas. "
                    "Consulta docs/compatibility.md.")
        return f"Opciones de CLI disponibles para {mode}"
    if shutil.which(config.csdm):
        check("Contrato CLI", cli_contract)

    settings = {}
    def load_settings():
        nonlocal settings
        candidate = read_json(config.csdm_settings)
        require(isinstance(candidate, dict), "settings.json no contiene un objeto.")
        settings = candidate
        return "Configuración existente; no se modifica"
    check("Configuración CS Demo Manager", load_settings)

    def game():
        require(config.cs2_dir is not None, "Falta paths.cs2_dir para capturar vídeo.")
        executable = platforms.cs2_executable(config.cs2_dir)
        require(executable.is_file(), f"No existe {executable}.")
        require(csdm_game_dir(settings) == config.cs2_dir.resolve(),
                "paths.cs2_dir y la instalación usada por CS Demo Manager no coinciden.")
        require(os.access(config.cs2_dir / "game/csgo", os.W_OK), "No se puede escribir en game/csgo.")
        info = config.cs2_dir / "game/csgo/steam.inf"
        if info.exists():
            versions["cs2"] = info.read_text().strip()
        return str(config.cs2_dir)
    if capture:
        check("Instalación CS2", game)
        check("Temporales CS2", lambda: assert_raw_dirs_empty(config))

    def csdm_ffmpeg():
        require(isinstance(settings.get("video"), dict), "Falta la configuración de vídeo de CS Demo Manager.")
        ff = settings["video"]["ffmpegSettings"]
        path = (Path(ff["customExecutableLocation"]) if ff.get("customLocationEnabled")
                else config.csdm_settings.parent / "ffmpeg/bin" /
                ("ffmpeg.exe" if platforms.system() == "Windows" else "ffmpeg"))
        require(path.is_file(), "Instala FFmpeg desde CS Demo Manager o configura su ubicación personalizada "
                "en Settings > Video; no basta con pasarlo a la CLI.")
        return str(path)
    if capture:
        check("FFmpeg registrado en CS Demo Manager", csdm_ffmpeg)

    def database():
        require(isinstance(settings.get("database"), dict), "Falta la conexión PostgreSQL de CS Demo Manager.")
        db = settings["database"]
        require(db.get("mode", "external") == "external",
                "Configura PostgreSQL externo en CS Demo Manager para esta PoC.")
        env = {k: v for k, v in os.environ.items() if not k.startswith("PG")}
        env.update(PGHOST=str(db["hostname"]), PGPORT=str(db["port"]), PGUSER=str(db["username"]),
                   PGPASSWORD=str(db["password"]), PGDATABASE=str(db["database"]), PGCONNECT_TIMEOUT="5")
        result = runner.run([config.psql, "-X", "-w", "-A", "-t", "-c", "SHOW server_version_num"],
                            timeout=10, env=env)
        version = int(result.stdout.strip())
        require(version >= 170000, "Se necesita PostgreSQL 17 o posterior.")
        versions["postgresql"] = str(version)
        return f"Conexión autenticada, versión {version}"
    check("PostgreSQL", database)

    def writable():
        config.data_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryFile(dir=config.data_dir):
            pass
        return f"{config.data_dir} ({shutil.disk_usage(config.data_dir).free / 2**30:.1f} GiB libres)"
    check("Directorio de trabajo", writable)
    if mode != "analysis":
        check("Steam abierto", lambda: require(platforms.process_running("steam", runner),
              "Abre Steam e inicia sesión; la autenticación se comprobará al descargar."))
    if capture:
        check("CS2 cerrado", lambda: require(not platforms.process_running("cs2", runner),
                                            "Cierra CS2 antes de grabar."))
    return checks, versions
