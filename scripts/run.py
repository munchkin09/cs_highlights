"""Use this checkout's virtual environment and config, independently of cwd."""
import os
from pathlib import Path
import sys


def main():
    root = Path(__file__).resolve().parents[1]
    python = root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not python.is_file():
        print("Primero ejecuta python scripts/install.py en este equipo.", file=sys.stderr)
        return 1
    env = {key: value for key, value in os.environ.items() if key not in {"PYTHONHOME", "PYTHONPATH"}}
    env["PYTHONNOUSERSITE"] = "1"
    config = os.environ.get("CSH_CONFIG", str(root / "config.local.toml"))
    os.execve(str(python), [str(python), "-m", "cs_highlights", "--config", config, *sys.argv[1:]], env)


if __name__ == "__main__":
    raise SystemExit(main())
