"""OS-specific paths, process checks and the native CSDM command boundary."""
import csv
import io
import os
from pathlib import Path
import platform
import shutil

from .errors import HighlightsError


def system() -> str:
    return platform.system()


def supports_capture() -> bool:
    return system() in {"Linux", "Windows"}


def config_home() -> Path:
    if system() == "Windows":
        return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData/Local")
    if system() == "Darwin":
        return Path.home() / "Library/Application Support"
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")


def csdm_settings_path() -> Path:
    # CSDM uses ~/.csdm on Windows and macOS, not Electron's userData path.
    folder = (config_home() / "csdm") if system() == "Linux" else Path.home() / ".csdm"
    return folder / "settings.json"


def steam_roots() -> list[Path]:
    if system() == "Windows":
        roots = []
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
                roots.append(Path(winreg.QueryValueEx(key, "SteamPath")[0]))
        except (ImportError, OSError):
            pass
        roots.append(Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")) / "Steam")
        return roots
    if system() == "Darwin":
        return [Path.home() / "Library/Application Support/Steam"]
    return [Path.home() / path for path in (
        ".steam/steam", ".local/share/Steam", ".local/share/steam",
        "snap/steam/common/.local/share/Steam")]


def cs2_executable(root: Path) -> Path:
    if system() == "Windows":
        return root / "game/bin/win64/cs2.exe"
    if system() == "Linux":
        return root / "game/cs2.sh"
    raise HighlightsError("CS2 no tiene versión nativa para macOS. Usa analyze para analizar demos; "
                          "la captura requiere Windows o Linux.")


def game_root_from_executable(executable: Path) -> Path:
    tail = ("game", "bin", "win64", "cs2.exe") if system() == "Windows" else ("game", "cs2.sh")
    if (tuple(p.casefold() for p in executable.parts[-len(tail):]) != tail
            or not executable.is_file()):
        raise HighlightsError(f"Configura Playback en CS Demo Manager con {'/'.join(tail)}.")
    return executable.resolve().parents[len(tail) - 1]


def default_tool(name: str) -> str:
    found = shutil.which(name)
    if found:
        return found
    candidates = []
    windows = system() == "Windows"
    if name == "csdm":
        if windows:
            candidates = [Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData/Local") /
                          "Programs/CS Demo Manager/csdm.cmd",
                          Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "CS Demo Manager/csdm.cmd"]
        elif system() == "Darwin":
            candidates = [Path("/Applications/CS Demo Manager.app/Contents/Resources/csdm")]
        else:
            candidates = [Path("/opt/CS Demo Manager/csdm"), Path("/usr/share/cs-demo-manager/csdm")]
    elif name == "psql":
        if windows:
            parent = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "PostgreSQL"
            candidates = [p / "bin/psql.exe" for p in sorted(parent.glob("*"), reverse=True)]
        elif system() == "Darwin":
            candidates = [Path(p) / "bin/psql" for p in (
                "/opt/homebrew/opt/postgresql@17", "/usr/local/opt/postgresql@17", "/Library/PostgreSQL/17")]
    elif name in {"ffmpeg", "ffprobe"}:
        candidates = [csdm_settings_path().parent / "ffmpeg/bin" / (name + (".exe" if windows else ""))]
    return str(next((p for p in candidates if p.is_file()), name))


def process_running(name: str, runner) -> bool:
    if system() == "Windows":
        # CSV has no translated headings; avoid parsing localized 'no tasks' messages.
        result = runner.run(["tasklist", "/FO", "CSV", "/NH"], timeout=10, check=False)
        rows = [row for row in csv.reader(io.StringIO(result.stdout)) if row]
        if result.returncode or not rows or any(len(row) < 2 or not row[1].isdigit() for row in rows):
            raise HighlightsError("No se pudo consultar la lista de procesos de Windows.")
        return any(row[0].casefold() == f"{name}.exe" for row in rows)
    executable = "steam_osx" if name == "steam" and system() == "Darwin" else name
    result = runner.run(["pgrep", "-x", executable], timeout=5, check=False)
    if result.returncode not in (0, 1):
        raise HighlightsError(f"No se pudo comprobar el proceso {name}.")
    return result.returncode == 0


def csdm_invocation(binary: str, arguments: list[str]) -> tuple[list[str], dict | None]:
    if system() != "Windows":
        return [binary, *arguments], None
    executable = Path(shutil.which(binary) or binary)
    if executable.suffix.lower() in {".cmd", ".bat"}:
        # Reproduce the official csdm.cmd without cmd.exe/%* expansion of demo paths.
        electron = executable.parent / "cs-demo-manager.exe"
        archive = executable.parent / "resources/app.asar"
        if not electron.is_file() or not archive.is_file():
            raise HighlightsError(f"No se encuentra la instalación de CS Demo Manager junto a {executable}.")
        env = dict(os.environ, ELECTRON_RUN_AS_NODE="1")
        return [str(electron), str(archive / "cli.js"), *arguments], env
    return [str(executable), *arguments], None
