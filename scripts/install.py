"""Install the project with the current Python on Windows, macOS, or Linux."""
from pathlib import Path
import os
import subprocess
import sys
import venv


def main():
    if sys.version_info < (3, 12):
        print("Se necesita Python 3.12 o posterior.", file=sys.stderr)
        return 1
    root = Path(__file__).resolve().parents[1]
    environment = root / ".venv"
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    env = {key: value for key, value in os.environ.items() if key not in {"PYTHONHOME", "PYTHONPATH"}}
    env["PYTHONNOUSERSITE"] = "1"
    try:
        if environment.exists() and not python.is_file():
            raise RuntimeError("La .venv existente no corresponde a este sistema o está incompleta. "
                               "Muévela antes de instalar; no se ha borrado.")
        if not environment.exists():
            venv.EnvBuilder(with_pip=True).create(environment)
        subprocess.run([str(python), "-c", "import sys; assert sys.version_info >= (3, 12)"],
                       check=True, env=env)
        has_pip = subprocess.run([str(python), "-m", "pip", "--version"], env=env,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
        if not has_pip:
            subprocess.run([str(python), "-m", "ensurepip", "--upgrade"], check=True, env=env)
        subprocess.run([str(python), "-m", "pip", "install", "--editable", str(root)], check=True, env=env)
        config = root / "config.local.toml"
        if config.exists():
            print(f"Se conserva {config}.")
        else:
            subprocess.run([str(python), "-m", "cs_highlights", "--config", str(config), "init"],
                           check=True, env=env)
        print("CLI instalada. Revisa config.local.toml y ejecuta: python scripts/run.py doctor")
        print("Dependencias externas y límites por sistema: docs/platforms.md")
        return 0
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"No se pudo instalar: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
