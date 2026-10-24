import argparse
from contextlib import ExitStack
import os
from pathlib import Path
import signal
import sys

from . import __version__
from .acquisition import discover_and_download
from .commands import Runner
from .config import create_config, load_config
from .csdm import CSDM
from .environment import prerequisites
from .errors import HighlightsError
from .pipeline import Pipeline
from .storage import exclusive_lock, now, write_json
from . import platforms


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Analiza demos de CS2 y genera vídeos de tus bajas en Windows o Linux.")
    default = platforms.config_home() / "cs-highlights/config.toml"
    result.add_argument("--config", type=Path, default=Path("config.toml") if Path("config.toml").exists() else default,
                        help="Archivo TOML (por defecto config.toml local o la carpeta de configuración del usuario)")
    result.add_argument("--version", action="version", version=__version__)
    commands = result.add_subparsers(dest="command", required=True)
    doctor = commands.add_parser("doctor", help="Comprueba dependencias y guarda un informe sin credenciales")
    doctor.add_argument("--mode", choices=("auto", "capture", "analysis", "download"), default="auto",
                        help="auto: análisis en macOS, captura en Windows/Linux")
    commands.add_parser("init", help="Crea una configuración para este sistema sin sobrescribir archivos existentes")
    analyze = commands.add_parser("analyze", help="Exporta análisis e intervalos sin ejecutar CS2 ni generar vídeo")
    analyze.add_argument("--mode", choices=("highlights", "deaths"), default="highlights")
    analyze.add_argument("demo", type=Path)
    commands.add_parser("download", help="Descarga demos de Steam sin iniciar una captura")
    run = commands.add_parser("run", help="Descubre partidas, descarga demos y procesa las pendientes")
    run.add_argument("--mode", choices=("highlights", "deaths"), default="highlights")
    process = commands.add_parser("process", help="Procesa una demo local o reanuda su trabajo")
    process.add_argument("--mode", choices=("highlights", "deaths"), default="highlights")
    process.add_argument("demo", type=Path)
    return result


def main(argv=None) -> int:
    args = parser().parse_args(argv)

    def interrupted(signum, frame):
        raise KeyboardInterrupt(f"Recibida señal {signal.Signals(signum).name}")

    previous = signal.signal(signal.SIGTERM, interrupted)
    try:
        if args.command == "init":
            create_config(args.config)
            print(f"Configuración creada: {args.config.resolve()}. Revisa SteamID64, rutas y herramientas.")
            return 0
        config = load_config(args.config)
        runner = Runner()
        mode = {"analyze": "analysis", "download": "download"}.get(args.command, "capture")
        if args.command == "doctor":
            mode = args.mode
            if mode == "auto":
                mode = "analysis" if platforms.system() == "Darwin" else "capture"
        print(f"Modo: {mode}", flush=True)
        checks, versions = prerequisites(config, runner, mode=mode)
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
            if mode == "capture":
                csdm.assert_idle()
            print(f"Entorno preparado para {mode}. La validación de una demo real aún debe ejecutarse.")
            return 0
        with ExitStack() as stack:
            stack.enter_context(exclusive_lock(config.data_dir / ".lock"))
            if mode == "capture":
                if config.cs2_dir is None:
                    raise HighlightsError("Falta paths.cs2_dir para capturar vídeo.")
                stack.enter_context(exclusive_lock(config.cs2_dir / "game/csgo/.cs-highlights.lock"))
                csdm.assert_idle()
            if args.command == "download":
                demos, failed = discover_and_download(csdm)
                print(f"Demos disponibles: {len(demos)} en {config.data_dir / 'downloads'}")
                return int(failed)
            pipeline = Pipeline(config, csdm, versions, report=lambda message: print(message, flush=True))
            selection_mode = getattr(args, "mode", "highlights")
            if args.command == "analyze":
                if selection_mode == "deaths":
                    pipeline.process(args.demo, analysis_only=True, mode=selection_mode)
                else:
                    pipeline.process(args.demo, analysis_only=True)
                return 0
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
                    if selection_mode == "deaths":
                        pipeline.process(demo, mode=selection_mode)
                    else:
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
