"""Bounded multi-sequence captures with independent clip validation and recovery."""
from dataclasses import dataclass, field
import os
from pathlib import Path
import shutil
import time
import uuid

from .environment import assert_raw_dirs_empty
from .errors import HighlightsError
from .storage import file_hash, now, read_json, write_json
from .video import check_space, replace_audio_with_silence, validate_video


@dataclass(frozen=True)
class Capture:
    key: str
    interval: object
    path: Path
    duration: float
    metadata: dict = field(default_factory=dict)
    focus_player: bool = True
    true_view: bool = False
    silent_audio: bool = False


def sequence_filename(number: int, item: Capture) -> str:
    return f"sequence-{number}-tick-{item.interval.start_tick}-to-{item.interval.end_tick}.mp4"


def _candidate(attempt: Path, item: Capture) -> Path | None:
    plan = attempt / "capture-plan.json"
    if plan.is_file():
        entries = read_json(plan)
        if not isinstance(entries, dict) or item.key not in entries:
            raise HighlightsError(f"Plan de captura incompatible en {plan}.")
        number = entries[item.key]
        if type(number) is not int or number < 1:
            raise HighlightsError(f"Número de secuencia inválido en {plan}.")
        found = list(attempt.rglob(sequence_filename(number, item)))
    else:
        # Attempts written by the previous per-clip workflow remain recoverable.
        found = list(attempt.rglob("*.mp4"))
    return found[0] if len(found) == 1 else None


def capture_many(pipeline, demo, job, manifest, save, stage, captures: list[Capture]) -> None:
    c, csdm, runner = pipeline.config, pipeline.csdm, pipeline.runner
    # A disconnected CLI may leave a daemon converting these very same attempt files.
    if captures:
        csdm.assert_idle()
    pending = []
    completed = set()
    attempts_to_clean = set()

    def publish(item, candidate):
        validate_video(candidate, item.duration, c, runner)
        item.path.parent.mkdir(parents=True, exist_ok=True)
        if candidate != item.path:
            os.replace(candidate, item.path)
        if item.silent_audio:
            replace_audio_with_silence(item.path, item.duration, c, runner)
        metadata = validate_video(item.path, item.duration, c, runner)
        manifest["clips"][item.key] = {**item.metadata, "sha256": file_hash(item.path), "probe": metadata}
        save(stage)
        completed.add(item.key)

    for item in captures:
        record = manifest["clips"].get(item.key)
        if item.path.is_file() and record and file_hash(item.path) == record.get("sha256"):
            validate_video(item.path, item.duration, c, runner)
            completed.add(item.key)
            continue
        # Intro publication can be interrupted while replacing its audio, before saving its hash.
        if item.key == "intro" and record is None and item.path.is_file():
            try:
                validate_video(item.path, item.duration, c, runner)
            except HighlightsError:
                pass
            else:
                publish(item, item.path)
                continue
        for relative in reversed(manifest["attempts"].setdefault(item.key, [])):
            attempt = (job / relative).resolve()
            if not attempt.is_relative_to((job / "render").resolve()):
                raise HighlightsError("Ruta de intento inválida en el manifiesto.")
            candidate = _candidate(attempt, item)
            if candidate is not None:
                try:
                    validate_video(candidate, item.duration, c, runner)
                except HighlightsError:
                    continue
                publish(item, candidate)
                attempts_to_clean.add(attempt)
                break
        else:
            pending.append(item)

    def cleanup():
        for attempt in attempts_to_clean:
            plan = attempt / "capture-plan.json"
            keys = set(read_json(plan)) if plan.is_file() else {
                key for key, paths in manifest["attempts"].items() if str(attempt.relative_to(job)) in paths}
            if keys <= completed:
                shutil.rmtree(attempt, ignore_errors=True)

    cleanup()
    while pending:
        # Do not bypass either the CS2 or daemon-queue checks between sessions.
        csdm.assert_idle()
        assert_raw_dirs_empty(c)
        batch = []
        duration = 0.0
        for item in pending[:c.capture_batch_size]:
            if batch and item.true_view != batch[0].true_view:
                break
            try:
                check_space(c, job, duration + item.duration)
            except HighlightsError:
                if not batch:
                    raise
                pipeline.report(f"Lote reducido a {len(batch)} clips por el espacio disponible.")
                break
            batch.append(item)
            duration += item.duration
        attempt = job / "render" / f"batch-{uuid.uuid4().hex}"
        attempt.mkdir(parents=True)
        write_json(attempt / "capture-plan.json", {item.key: number for number, item in enumerate(batch, 1)})
        for item in batch:
            manifest["attempts"][item.key].append(str(attempt.relative_to(job)))
        record = {"attempt": str(attempt.relative_to(job)), "clips": [item.key for item in batch],
                  "duration": duration, "started_at": now(), "status": "running"}
        manifest.setdefault("capture_batches", []).append(record)
        save(stage)
        pipeline.report(f"Sesión de CS2: {len(batch)} clips ({duration:.1f} s); "
                        "el juego se cierra al acabar el lote para convertirlos.")
        started = time.monotonic()
        try:
            log = job / "logs" / f"{attempt.name}.log"
            if len(batch) == 1:
                item = batch[0]
                csdm.render(demo, item.interval, attempt, log, true_view=item.true_view,
                            focus_player=item.focus_player, record_audio=True)
            else:
                csdm.render_batch(demo, batch, attempt, log)
            for item in batch:
                candidate = _candidate(attempt, item)
                if candidate is None:
                    raise HighlightsError(f"Falta el MP4 de {item.key} en {attempt}.")
                publish(item, candidate)
            record["status"] = "complete"
        except (HighlightsError, OSError, KeyboardInterrupt):
            record["status"] = "interrupted"
            raise
        finally:
            record["elapsed_seconds"] = round(time.monotonic() - started, 3)
            save(stage)
        attempts_to_clean.add(attempt)
        cleanup()
        del pending[:len(batch)]
