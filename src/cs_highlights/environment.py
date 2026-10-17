"""Linux prerequisites and non-destructive checks of the CSDM integration."""
import os
from pathlib import Path
import platform
import re
import shutil
import tempfile

from .commands import Runner
from .config import Config
from .errors import HighlightsError
from .storage import read_json


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
        if executable.name != "cs2.sh" or not executable.is_file():
            raise HighlightsError("Configura la ruta de CS2 en CS Demo Manager como game/cs2.sh.")
        return executable.resolve().parent.parent
    home = Path.home()
    candidates = [home / ".steam/steam", home / "snap/steam/common/.local/share/Steam",
                  home / ".local/share/steam"]
    steam = next((p for p in candidates if p.exists()), candidates[-1])
    if "/snap/" in str(steam.resolve()) or "/.var/app/" in str(steam.resolve()):
        raise HighlightsError("La PoC requiere Steam nativo, instalado mediante el gestor de paquetes.")
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


def prerequisites(config: Config, runner: Runner) -> tuple[list[dict], dict]:
    checks = []
    versions = {"python": platform.python_version(), "platform": platform.platform()}

    def check(name, action):
        try:
            detail = action()
            checks.append({"name": name, "ok": True, "detail": str(detail or "OK")})
        except (HighlightsError, OSError, ValueError, KeyError, TypeError, AttributeError, IndexError) as exc:
            checks.append({"name": name, "ok": False, "detail": str(exc)})

    def require(condition, message):
        if not condition:
            raise HighlightsError(message)

    check("Linux", lambda: require(platform.system() == "Linux", "La captura de CS2 requiere Linux; "
                                   "el análisis y las pruebas unitarias se pueden ejecutar aquí."))
    check("Sesión gráfica", lambda: require(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"),
                                           "Ejecuta la CLI desde tu escritorio Linux."))
    for name in ("csdm", "ffmpeg", "ffprobe", "psql"):
        def executable(name=name):
            binary = getattr(config, name)
            require(shutil.which(binary), f"No se encuentra {binary}.")
            flag = "help" if name == "csdm" else "--version" if name == "psql" else "-version"
            result = runner.run([binary, flag], timeout=15)
            version = result.stdout.strip().splitlines()[0]
            versions[name] = version
            return version
        check(name, executable)

    def cli_contract():
        for command, options in ((["dl-valve"], ["--output"]),
                                 (["video"], ["--focus-player", "--recording-output", "--ffmpeg-input-parameters"]),
                                 (["json"], ["--output-folder"])):
            output = runner.run([config.csdm, *command, "--help"], timeout=20).stdout
            require(all(option in output for option in options),
                    "La versión de CS Demo Manager no ofrece las opciones de CLI requeridas. "
                    "Consulta docs/compatibility.md.")
        return "Descarga, exportación JSON y renderizado disponibles"
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
        require((config.cs2_dir / "game/cs2.sh").is_file(), f"No existe game/cs2.sh en {config.cs2_dir}.")
        require(csdm_game_dir(settings) == config.cs2_dir.resolve(),
                "paths.cs2_dir y la instalación usada por CS Demo Manager no coinciden.")
        require(os.access(config.cs2_dir / "game/csgo", os.W_OK), "No se puede escribir en game/csgo.")
        info = config.cs2_dir / "game/csgo/steam.inf"
        if info.exists():
            versions["cs2"] = info.read_text().strip()
        return str(config.cs2_dir)
    check("Instalación CS2", game)
    check("Temporales CS2", lambda: assert_raw_dirs_empty(config))

    def csdm_ffmpeg():
        require(isinstance(settings.get("video"), dict), "Falta la configuración de vídeo de CS Demo Manager.")
        ff = settings["video"]["ffmpegSettings"]
        path = (Path(ff["customExecutableLocation"]) if ff.get("customLocationEnabled")
                else config.csdm_settings.parent / "ffmpeg/bin/ffmpeg")
        require(path.is_file(), "Instala FFmpeg desde CS Demo Manager o configura su ubicación personalizada "
                "en Settings > Video; no basta con pasarlo a la CLI.")
        return str(path)
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
    check("Steam abierto", lambda: runner.run(["pgrep", "-x", "steam"], timeout=5) and
          "Proceso encontrado; la autenticación se comprobará al descargar")
    check("CS2 cerrado", lambda: require(runner.run(["pgrep", "-x", "cs2"], timeout=5, check=False).returncode == 1,
                                        "Cierra CS2 antes de usar la PoC."))
    return checks, versions
