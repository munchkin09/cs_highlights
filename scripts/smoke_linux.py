"""One short real recording; intentionally separate from completed full-match jobs."""
import argparse
from contextlib import ExitStack
from dataclasses import replace
import os
from pathlib import Path
import shutil
import sys
import uuid

from cs_highlights.acquisition import discover_and_download
from cs_highlights.analysis import normalize
from cs_highlights.commands import Runner
from cs_highlights.config import load_config
from cs_highlights.csdm import CSDM
from cs_highlights.environment import assert_raw_dirs_empty, prerequisites
from cs_highlights.errors import HighlightsError
from cs_highlights.pipeline import import_demo
from cs_highlights.selection import select_kills
from cs_highlights.storage import exclusive_lock, now, read_json, write_json
from cs_highlights.video import check_space, validate_video


def main():
    parser = argparse.ArgumentParser(description="Descarga, analiza y graba hasta diez segundos de una demo real.")
    parser.add_argument("--config", type=Path, default=Path("config.toml"))
    parser.add_argument("demo", nargs="?", type=Path)
    args = parser.parse_args()
    try:
        c = load_config(args.config)
        runner = Runner()
        checks, versions = prerequisites(c, runner)
        for check in checks:
            print(f"{'OK' if check['ok'] else 'ERROR'} · {check['name']}: {check['detail']}", flush=True)
        if not all(check["ok"] for check in checks):
            return 1
        backend = CSDM(c, runner)
        with ExitStack() as stack:
            stack.enter_context(exclusive_lock(c.data_dir / ".lock"))
            stack.enter_context(exclusive_lock(c.cs2_dir / "game/csgo/.cs-highlights.lock"))
            backend.assert_idle()
            if args.demo:
                demos = [args.demo]
            else:
                print("Descargando las partidas disponibles de Steam… (puede tardar varios minutos)", flush=True)
                demos, failed = discover_and_download(backend)
                print(f"Demos disponibles: {len(demos)}. Registro: {c.data_dir / 'downloads/download.log'}", flush=True)
                if failed:
                    print("Algunas descargas fallaron; se intentará grabar una de las disponibles.")
            for source in demos:
                print(f"Analizando {source.name}…", flush=True)
                checksum, demo = import_demo(source, c)
                folder = c.data_dir / "smoke" / uuid.uuid4().hex
                raw = backend.export(demo, folder / "export", folder / "analysis.log")
                analysis = normalize(read_json(raw))
                intervals = select_kills(analysis, c.steam_id, c.before_seconds, c.after_seconds)
                if not intervals:
                    print("Sin bajas seleccionables; probando la siguiente demo.", flush=True)
                    continue
                interval = intervals[0]
                # Center the short test on its first kill, even if the configured pre-roll is long.
                focus_tick = interval.kill_ticks[0] if interval.kill_ticks else interval.start_tick
                start = max(interval.start_tick, focus_tick - int(5 * analysis.tickrate))
                interval = replace(interval, start_tick=start,
                                   end_tick=min(interval.end_tick, start + int(10 * analysis.tickrate)))
                duration = (interval.end_tick - interval.start_tick) / analysis.tickrate
                backend.assert_idle()
                assert_raw_dirs_empty(c)
                check_space(c, folder, duration)
                attempt = folder / "capture"
                print(f"Grabando {duration:.1f} s de {source.name}…", flush=True)
                backend.render(demo, interval, attempt, folder / "render.log")
                files = list(attempt.rglob("*.mp4"))
                if len(files) != 1:
                    raise HighlightsError(f"Se esperaba un único MP4 en {attempt}.")
                probe = validate_video(files[0], duration, c, runner)
                output = folder / "smoke.mp4"
                os.replace(files[0], output)
                write_json(folder / "validation.json", {
                    "date": now(), "versions": versions, "demo_sha256": checksum,
                    "source": str(source), "interval": interval.to_dict(), "probe": probe,
                    "automatic_validation": "passed", "visual_review": "pending", "output": str(output),
                })
                shutil.rmtree(attempt)
                print(f"MP4 validado: {output}\nPendiente: revisar perspectiva, baja y audio visualmente.")
                return 0
            raise HighlightsError("No hay una demo disponible con bajas del jugador configurado.")
    except (HighlightsError, OSError) as exc:
        print(f"ERROR · {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Interrumpido. Comprueba CS2 y la cola de CS Demo Manager antes de reintentar.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
