import os
from dataclasses import replace
from pathlib import Path
import shutil
import uuid
from types import SimpleNamespace

from .analysis import normalize
from .config import Config
from .csdm import CSDM
from .capture import Capture, capture_many
from .errors import HighlightsError
from .selection import select_kills
from .storage import file_hash, now, read_json, write_json
from .video import concatenate, validate_video


def capture_segments(intervals, tickrate: float):
    """Bound temporary raw frames while preserving every tick of a selected play."""
    limit = max(1, int(10 * tickrate))
    segments = []
    for interval in intervals:
        length = interval.end_tick - interval.start_tick
        count = (length + limit - 1) // limit
        # Balance the pieces so a long play never ends in a sub-frame tail.
        for index in range(count):
            start = interval.start_tick + length * index // count
            end = interval.start_tick + length * (index + 1) // count
            segments.append(replace(interval, start_tick=start, end_tick=end,
                                    kill_ticks=tuple(t for t in interval.kill_ticks if start <= t < end)))
    return segments


def import_demo(source: Path, config: Config) -> tuple[str, Path]:
    source = source.expanduser().resolve()
    if source.suffix.lower() != ".dem" or not source.is_file():
        raise HighlightsError(f"No existe una demo .dem en {source}.")
    with source.open("rb") as file:
        if file.read(8) != b"PBDEMS2\0":
            raise HighlightsError(f"{source} no es una demo CS2 descomprimida válida.")
    checksum = file_hash(source)
    match_dir = config.data_dir / "matches" / checksum
    match_dir.mkdir(parents=True, exist_ok=True)
    destination = match_dir / "demo.dem"
    if not destination.exists() or file_hash(destination) != checksum:
        required = source.stat().st_size + int(config.reserve_gib * 2**30)
        if shutil.disk_usage(match_dir).free < required:
            raise HighlightsError("No hay espacio para conservar una copia de la demo.")
        temporary = destination.with_suffix(".partial")
        shutil.copyfile(source, temporary)
        if file_hash(temporary) != checksum:
            raise HighlightsError("La demo cambió durante la copia. Reintenta cuando termine la descarga.")
        os.replace(temporary, destination)
    source_info = source.with_suffix(".dem.info")
    if source_info.is_file() and source_info.resolve() != destination.with_suffix(".dem.info"):
        shutil.copyfile(source_info, destination.with_suffix(".dem.info"))
    return checksum, destination


class Pipeline:
    def __init__(self, config: Config, csdm: CSDM, versions: dict | None = None, report=print):
        self.config = config
        self.csdm = csdm
        self.runner = csdm.runner
        self.versions = versions or {}
        self.report = report

    def process(self, source: Path, *, analysis_only: bool = False, mode: str = "highlights") -> Path | None:
        if mode == "deaths":
            from .death_pipeline import DeathPipeline
            return DeathPipeline(self).process(source, analysis_only=analysis_only)
        if mode != "highlights":
            raise HighlightsError(f"Modo de análisis desconocido: {mode}.")
        checksum, demo = import_demo(source, self.config)
        job = demo.parent / self.config.fingerprint
        job.mkdir(parents=True, exist_ok=True)
        manifest_path = job / "manifest.json"
        manifest = read_json(manifest_path) if manifest_path.exists() else {
            "schema_version": 1, "demo_sha256": checksum, "settings": self.config.output_settings(),
            "created_at": now(), "clips": {}, "attempts": {},
        }
        if (not isinstance(manifest, dict) or manifest.get("schema_version") != 1
                or manifest.get("demo_sha256") != checksum
                or manifest.get("settings") != self.config.output_settings()
                or not isinstance(manifest.get("clips"), dict)
                or not isinstance(manifest.get("attempts"), dict)):
            raise HighlightsError(f"Manifiesto incompatible en {manifest_path}; conserva la carpeta para inspección.")
        manifest["versions"] = self.versions
        manifest["source"] = str(source.resolve())
        manifest["error"] = None

        def save(stage: str):
            manifest.update(stage=stage, updated_at=now())
            write_json(manifest_path, manifest)

        stage = "analysis"
        try:
            output = job / "highlights.mp4"
            if not analysis_only and manifest.get("status") == "complete" and output.exists():
                if file_hash(output) == manifest.get("output_sha256"):
                    validate_video(output, manifest["expected_duration"], self.config, self.runner)
                    self.report(f"Ya completo: {output}")
                    return output
            previous_status = manifest.get("status")
            manifest["status"] = "running"
            save(stage)
            raw = job / "raw.json"
            if not raw.exists() or file_hash(raw) != manifest.get("raw_sha256"):
                export_dir = job / "exports" / uuid.uuid4().hex
                exported = self.csdm.export(demo, export_dir, job / "logs/analysis.log")
                # Validate before publishing the raw cache; keep failed exports for diagnosis.
                normalize(read_json(exported))
                os.replace(exported, raw)
                manifest["raw_sha256"] = file_hash(raw)
                save(stage)
            analysis = normalize(read_json(raw))
            write_json(job / "analysis.json", analysis.to_dict())

            stage = "selection"
            save(stage)
            intervals = select_kills(analysis, self.config.steam_id,
                                     self.config.before_seconds, self.config.after_seconds)
            segments = capture_segments(intervals, analysis.tickrate)
            write_json(job / "intervals.json", {"schema_version": 1, "tickrate": analysis.tickrate,
                                              "intervals": [i.to_dict() for i in intervals],
                                              "capture_segments": [i.to_dict() for i in segments]})
            if analysis_only:
                # Keep a completed capture reusable; analysis never deletes/revalidates its media.
                manifest["status"] = previous_status if previous_status == "complete" else "analyzed"
                manifest["analysis_completed_at"] = now()
                save("done")
                self.report(f"Análisis e intervalos: {job}")
                return job / "analysis.json"
            if not intervals:
                manifest.update(status="no_highlights", reason="No hay bajas contra enemigos ni clutches ganados seleccionables.")
                save("done")
                self.report(f"Sin vídeo: {manifest['reason']} ({source.name})")
                return None
            manifest["reason"] = None
            (job / "clips").mkdir(exist_ok=True)
            stage = "render"
            save(stage)
            captures = []
            intro = self.intro_capture(analysis, job)
            if intro is not None:
                captures.append(intro)
            for index, interval in enumerate(segments, 1):
                name = f"{index:04d}"
                captures.append(Capture(name, interval, job / "clips" / f"{name}.mp4",
                                        (interval.end_tick - interval.start_tick) / analysis.tickrate,
                                        {"interval": interval.to_dict()}))
            self.report(f"{len(intervals)} jugadas en {len(segments)} tramos de hasta 10 s; "
                        f"hasta {self.config.capture_batch_size} clips por sesión de CS2.")
            capture_many(self, demo, job, manifest, save, stage, captures)
            clips = [item.path for item in captures]
            expected_duration = sum(item.duration for item in captures)

            stage = "concatenate"
            save(stage)
            metadata = concatenate(clips, output, expected_duration, self.config, self.runner)
            manifest.update(status="complete", output=str(output), output_sha256=file_hash(output),
                            expected_duration=expected_duration, probe=metadata, completed_at=now())
            save("done")
            self.report(f"Vídeo: {output}")
            return output
        except (HighlightsError, OSError, ValueError, KeyboardInterrupt) as exc:
            manifest.update(status="interrupted" if isinstance(exc, KeyboardInterrupt) else "failed",
                            error={"stage": stage, "message": str(exc) or "Interrumpido (Ctrl+C o señal de terminación)"})
            save(stage)
            if isinstance(exc, KeyboardInterrupt):
                raise
            raise HighlightsError(f"{source.name}: falló {stage}: {exc}") from exc

    def intro_capture(self, analysis, job, *, silent_audio=False):
        if self.config.intro_seconds <= 0 or analysis.intro_start_tick is None or analysis.intro_end_tick is None:
            return None
        start_tick = analysis.intro_start_tick
        end_tick = min(analysis.intro_end_tick,
                       start_tick + max(1, round(self.config.intro_seconds * analysis.tickrate)))
        duration = (end_tick - start_tick) / analysis.tickrate
        if duration < 1 / analysis.tickrate:
            return None
        return Capture("intro", SimpleNamespace(start_tick=start_tick, end_tick=end_tick),
                       job / "clips" / "0000.mp4", duration,
                       {"duration": duration, "start_tick": start_tick, "end_tick": end_tick,
                        "default_demo_camera": True, "audio_silenced": silent_audio},
                       focus_player=False, silent_audio=silent_audio)
