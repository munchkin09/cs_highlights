import argparse
from contextlib import ExitStack
import os
from pathlib import Path
import signal
import sys

from . import __version__
from .acquisition import discover_and_download
from .commands import Runner
from .config import load_config
from .csdm import CSDM
from .environment import prerequisites
from .errors import HighlightsError
from .pipeline import Pipeline
from .storage import exclusive_lock, now, write_json


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Genera vídeos de tus bajas de CS2 en tu PC Linux.")
    default = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "cs-highlights/config.toml"
    result.add_argument("--config", type=Path, default=Path("config.toml") if Path("config.toml").exists() else default,
                        help="Archivo TOML (por defecto config.toml local o ~/.config/cs-highlights/config.toml)")
    result.add_argument("--version", action="version", version=__version__)
    commands = result.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor", help="Comprueba dependencias y guarda un informe sin credenciales")
    commands.add_parser("run", help="Descubre partidas, descarga demos y procesa las pendientes")
    process = commands.add_parser("process", help="Procesa una demo local o reanuda su trabajo")
    process.add_argument("demo", type=Path)
    return result


def main(argv=None) -> int:
    args = parser().parse_args(argv)

    def interrupted(signum, frame):
        raise KeyboardInterrupt(f"Recibida señal {signal.Signals(signum).name}")

    previous = signal.signal(signal.SIGTERM, interrupted)
    try:
        config = load_config(args.config)
        runner = Runner()
        checks, versions = prerequisites(config, runner)
        versions["cs_highlights"] = __version__
        for check in checks:
            print(f"{'OK' if check['ok'] else 'ERROR'} · {check['name']}: {check['detail']}", flush=True)
        report = {"date": now(), "versions": versions, "checks": checks,
                  "live_demo_validated": False}
        if config.data_dir.exists() and os.access(config.data_dir, os.W_OK):
            write_json(config.data_dir / "environment.json", report)
        if not all(check["ok"] for check in checks):
            return 1
        csdm = CSDM(config, runner)
        if args.command == "doctor":
            csdm.assert_idle()
            print("Entorno preparado. La validación de una demo real aún debe ejecutarse.")
            return 0
        with ExitStack() as stack:
            stack.enter_context(exclusive_lock(config.data_dir / ".lock"))
            stack.enter_context(exclusive_lock(config.cs2_dir / "game/csgo/.cs-highlights.lock"))
            csdm.assert_idle()
            pipeline = Pipeline(config, csdm, versions, report=lambda message: print(message, flush=True))
            failed = False
            if args.command == "process":
                demos = [args.demo]
            else:
                try:
                    demos, failed = discover_and_download(csdm)
                    if failed:
                        print("Algunas descargas fallaron. Se procesarán las disponibles; consulta downloads/download.log.",
                              file=sys.stderr)
                except HighlightsError as exc:
                    # A Steam/network outage must not discard already downloaded work.
                    print(str(exc), file=sys.stderr)
                    demos = sorted((config.data_dir / "downloads").glob("*.dem"))
                    failed = True
            if not demos:
                print("No hay demos disponibles para procesar.")
            for demo in demos:
                try:
                    pipeline.process(demo)
                except (HighlightsError, OSError) as exc:
                    print(f"ERROR · {exc}", file=sys.stderr)
                    failed = True
            return 1 if failed else 0
    except (HighlightsError, OSError) as exc:
        print(f"ERROR · {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt as exc:
        print(f"Interrumpido{': ' + str(exc) if str(exc) else ''}. Los clips validados se conservan. Un trabajo enviado a CS Demo Manager "
              "puede seguir activo: revisa su cola antes de reanudar.", file=sys.stderr)
        return 130
    finally:
        signal.signal(signal.SIGTERM, previous)
