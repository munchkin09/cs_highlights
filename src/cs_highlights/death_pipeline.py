"""Resumable review workflow for deaths caused by opposing players."""
import json
import hashlib
import os
from pathlib import Path
import shutil
import uuid
from types import SimpleNamespace

from .analysis import normalize
from .death_review import _freeze_time, compose, render_map_frames
from .deaths import diagnose, select_deaths
from .capture import Capture, capture_many
from .errors import HighlightsError
from .pipeline import import_demo
from .storage import file_hash, now, read_json, write_json
from .video import concatenate, validate_video


class DeathPipeline:
    def __init__(self, pipeline):
        self.base = pipeline
        self.config = pipeline.config
        self.csdm = pipeline.csdm
        self.runner = pipeline.runner
        self.report = pipeline.report
        self.versions = pipeline.versions

    def process(self, source: Path, *, analysis_only: bool = False) -> Path | None:
        self.csdm.assert_review_capabilities()
        checksum, demo = import_demo(source, self.config)
        job = demo.parent / self.config.fingerprint_for_mode("deaths")
        analysis_job = job
        job.mkdir(parents=True, exist_ok=True)
        manifest_path = job / "manifest.json"
        settings = self.config.output_settings("deaths")
        manifest = read_json(manifest_path) if manifest_path.exists() else {
            "schema_version": 1, "demo_sha256": checksum, "settings": settings,
            "created_at": now(), "clips": {}, "attempts": {},
        }
        if (not isinstance(manifest, dict) or manifest.get("schema_version") != 1
                or manifest.get("demo_sha256") != checksum or manifest.get("settings") != settings
                or not isinstance(manifest.get("clips"), dict)
                or not isinstance(manifest.get("attempts"), dict)
                or any(not isinstance(value, dict) for value in manifest.get("clips", {}).values())
                or any(not isinstance(value, list) or any(not isinstance(item, str) for item in value)
                       for value in manifest.get("attempts", {}).values())):
            raise HighlightsError(f"Manifiesto de deaths incompatible en {manifest_path}.")
        manifest.update(versions=self.versions, source=str(source.resolve()), error=None, mode="deaths")

        def save(stage: str):
            manifest.update(stage=stage, updated_at=now())
            write_json(manifest_path, manifest)

        stage = "analysis"
        try:
            previous_status = manifest.get("status")
            manifest["status"] = "running"
            save(stage)
            raw = job / "raw.json"
            if not raw.exists() or file_hash(raw) != manifest.get("raw_sha256"):
                exported = self.csdm.export(demo, job / "exports" / uuid.uuid4().hex, job / "logs/analysis.log")
                normalize(read_json(exported))
                os.replace(exported, raw)
                manifest["raw_sha256"] = file_hash(raw)
                save(stage)
            analysis = normalize(read_json(raw))
            stage = "selection"
            save(stage)
            plays = select_deaths(analysis, self.config.steam_id,
                                  self.config.death_before_seconds, self.config.death_after_seconds)
            if not plays:
                manifest.update(status="no_highlights", reason="No hay muertes causadas por enemigos seleccionables.")
                write_json(job / "deaths.json", {"schema_version": 1, "plays": []})
                save("done")
                self.report(f"Sin muertes seleccionables: {source.name}")
                return None
            if self.csdm.position_count(analysis.checksum) == 0:
                self.report("No hay posiciones guardadas; se analizará la demo con --analyze-positions.")
                self.csdm.analyze_positions(demo, job / "logs/positions.log")
            if self.csdm.position_count(analysis.checksum) == 0:
                raise HighlightsError("CS Demo Manager terminó el análisis, pero no guardó posiciones de jugadores.")
            metadata = self.csdm.map_metadata(analysis.map_name)
            if metadata is None:
                raise HighlightsError(f"Faltan el radar o los metadatos del mapa {analysis.map_name} en CS Demo Manager.")
            if analysis.map_name.casefold() == "de_nuke" and metadata.get("lower_radar") is None:
                raise HighlightsError("Falta el radar de la planta inferior de Nuke en CS Demo Manager.")
            map_digest = hashlib.sha256(
                metadata["radar"] + (metadata.get("lower_radar") or b"")
                + json.dumps({key: metadata[key] for key in
                              ("position_x", "position_y", "threshold_z", "scale", "radar_size")},
                             sort_keys=True).encode()
            ).hexdigest()
            # Keep raw analysis reusable while giving each map-resource set its own
            # output directory, so updated radar assets never replace old videos.
            manifest.update(status="analysis_cached", map_resources_sha256=map_digest,
                            map_variant=f"{analysis_job.name}-{map_digest[:12]}", updated_at=now())
            write_json(manifest_path, manifest)
            job = analysis_job.parent / f"{analysis_job.name}-{map_digest[:12]}"
            job.mkdir(parents=True, exist_ok=True)
            manifest_path = job / "manifest.json"
            manifest = read_json(manifest_path) if manifest_path.exists() else {
                "schema_version": 1, "demo_sha256": checksum, "settings": settings,
                "created_at": now(), "clips": {}, "attempts": {},
            }
            variant_exists = manifest_path.exists()
            if (not isinstance(manifest, dict) or manifest.get("schema_version") != 1
                    or manifest.get("demo_sha256") != checksum or manifest.get("settings") != settings
                    or not isinstance(manifest.get("clips"), dict)
                    or not isinstance(manifest.get("attempts"), dict)
                    or any(not isinstance(value, dict) for value in manifest.get("clips", {}).values())
                    or any(not isinstance(value, list) or any(not isinstance(item, str) for item in value)
                           for value in manifest.get("attempts", {}).values())):
                raise HighlightsError(f"Manifiesto de deaths incompatible en {manifest_path}.")
            previous_status = manifest.get("status")
            resources_changed = (not variant_exists
                                 or manifest.get("map_resources_sha256") != map_digest)
            manifest.update(versions=self.versions, source=str(source.resolve()), error=None, mode="deaths",
                            map_resources_sha256=map_digest)
            output = job / "deaths.mp4"
            if (not analysis_only and manifest.get("status") == "complete" and not resources_changed
                    and output.is_file() and file_hash(output) == manifest.get("output_sha256")):
                validate_video(output, manifest["expected_duration"], self.config, self.runner)
                self.report(f"Ya completo: {output}")
                return output

            all_positions = []
            diagnostics = []
            for play in plays:
                positions = self.csdm.player_positions(analysis.checksum, play.start_tick, play.end_tick)
                positions = [point for point in positions
                             if point["round_number"] == play.round_number
                             and point["player_steam_id"] in {play.victim_steam_id, play.killer_steam_id}]
                for point in positions:
                    point["start_tick"] = play.start_tick
                    point["end_tick"] = play.end_tick
                    point["play"] = len(diagnostics) + 1
                all_positions.extend(positions)
                review = diagnose(play, analysis, positions)
                for player_id in (play.victim_steam_id, play.killer_steam_id):
                    starting = [point for point in positions
                                if point["player_steam_id"] == player_id
                                and abs(point["tick"] - play.start_tick) <= analysis.tickrate / 2]
                    if not any(point["is_alive"] for point in starting):
                        review["not_evaluable"].append(f"perspective_unavailable_at_start:{player_id}")
                diagnostics.append({"play": play.to_dict(), "review": review})
            write_json(job / "deaths.json", {"schema_version": 1, "map": analysis.map_name,
                                              "tickrate": analysis.tickrate, "plays": diagnostics})
            write_json(job / "positions.json", {"schema_version": 1, "positions": all_positions})
            manifest["play_count"] = len(plays)
            if analysis_only:
                manifest["status"] = previous_status if previous_status == "complete" else "analyzed"
                manifest["analysis_completed_at"] = now()
                save("done")
                self.report(f"Diagnóstico y recorridos: {job}")
                return job / "deaths.json"

            stage = "render"
            (job / "clips").mkdir(exist_ok=True)
            (job / "perspectives").mkdir(exist_ok=True)
            total_duration = 0.0
            composites = []
            tickrate = analysis.tickrate
            intro = self.base.intro_capture(analysis, job, silent_audio=True)
            captures = [intro] if intro is not None else []
            for index, play in enumerate(plays, 1):
                for role, player_id in (("victim", play.victim_steam_id), ("killer", play.killer_steam_id)):
                    captures.extend(self._perspective_captures(job, play, index, f"{index:04d}",
                                                              role, player_id, tickrate))
            capture_many(self.base, demo, job, manifest, save, stage, captures)
            if intro is not None:
                composites.append(intro.path)
                total_duration += intro.duration
            for index, play in enumerate(plays, 1):
                label = f"{index:04d}"
                clip_duration = (play.end_tick - play.start_tick) / tickrate
                total_duration += clip_duration
                raw_clips = []
                for role, player_id in (("victim", play.victim_steam_id), ("killer", play.killer_steam_id)):
                    raw_clips.append(self._capture_perspective(
                        demo, job, manifest, save, stage, play, index, label,
                        role, player_id, tickrate, clip_duration))

                frame_dir = job / "frames" / label
                review = diagnostics[index - 1]["review"]
                player_positions = [point for point in all_positions if point["play"] == index]
                alive_by_player = {}
                for player_id in (play.victim_steam_id, play.killer_steam_id):
                    samples = sorted((point for point in player_positions
                                      if point["player_steam_id"] == player_id), key=lambda p: p["tick"])
                    alive_by_player[player_id] = samples
                frame_count, rendered_duration = render_map_frames(
                    play, player_positions, metadata, {"tickrate": tickrate}, review, frame_dir, self.config)
                output = job / "clips" / f"{label}.mp4"
                output_duration = min(clip_duration, rendered_duration)
                composite = manifest.get("clips", {}).get(label)
                if (resources_changed or not (output.is_file() and composite
                                               and composite.get("sha256") == file_hash(output))):
                    victim_freeze = max(1 / self.config.fps,
                                        (play.death_tick - play.start_tick) / tickrate)
                    killer_freeze = _freeze_time(alive_by_player[play.killer_steam_id],
                                                 play.start_tick, tickrate, self.config.fps)
                    compose(*raw_clips, frame_dir, output, output_duration, self.config, self.runner,
                            victim_freeze=min(victim_freeze, output_duration - 1 / self.config.fps),
                            killer_freeze=killer_freeze)
                    manifest["clips"][label] = {"sha256": file_hash(output), "play": index,
                                                "frames": frame_count, "duration": output_duration}
                    save(stage)
                validate_video(output, output_duration, self.config, self.runner)
                shutil.rmtree(frame_dir, ignore_errors=True)
                composites.append(output)

            stage = "concatenate"
            save(stage)
            output = job / "deaths.mp4"
            meta = concatenate(composites, output, total_duration, self.config, self.runner)
            manifest.update(status="complete", output=str(output), output_sha256=file_hash(output),
                            expected_duration=total_duration, probe=meta, completed_at=now())
            save("done")
            self.report(f"Vídeo de análisis de muertes: {output}")
            return output
        except (HighlightsError, OSError, ValueError, KeyError, TypeError, KeyboardInterrupt) as exc:
            manifest.update(status="interrupted" if isinstance(exc, KeyboardInterrupt) else "failed",
                            error={"stage": stage, "message": str(exc) or "Interrumpido"})
            save(stage)
            if isinstance(exc, KeyboardInterrupt):
                raise
            raise HighlightsError(f"{source.name}: falló el modo deaths en {stage}: {exc}") from exc

    def _capture_perspective(self, demo, job, manifest, save, stage, play, index, label,
                             role, player_id, tickrate, duration) -> Path:
        name = f"{label}-{role}"
        captures = self._perspective_captures(job, play, index, label, role, player_id, tickrate)
        paths = [item.path for item in captures]
        output = job / "perspectives" / f"{name}.mp4"
        record = manifest["clips"].get(name)
        if not (output.is_file() and record and file_hash(output) == record.get("sha256")):
            metadata = concatenate(paths, output, duration, self.config, self.runner, clip_directory="segments")
            manifest["clips"][name] = {"sha256": file_hash(output), "role": role,
                                       "player_steam_id": player_id, "play": index,
                                       "segments": len(paths), "probe": metadata}
            save(stage)
        validate_video(output, duration, self.config, self.runner)
        return output

    @staticmethod
    def _perspective_captures(job, play, index, label, role, player_id, tickrate):
        captures = []
        for segment_index, (start_tick, end_tick) in enumerate(
                _review_segments(play.start_tick, play.end_tick, tickrate), 1):
            name = f"{label}-{role}-s{segment_index:02d}"
            captures.append(Capture(
                name, SimpleNamespace(start_tick=start_tick, end_tick=end_tick, player_steam_id=player_id),
                job / "perspectives" / "segments" / f"{name}.mp4", (end_tick - start_tick) / tickrate,
                {"role": role, "player_steam_id": player_id, "play": index,
                 "segment": segment_index, "true_view_requested": True}, true_view=True))
        return captures


def _review_segments(start_tick: int, end_tick: int, tickrate: float) -> list[tuple[int, int]]:
    limit = max(1, int(10 * tickrate))
    length = end_tick - start_tick
    count = (length + limit - 1) // limit
    return [(start_tick + length * index // count,
             start_tick + length * (index + 1) // count) for index in range(count)]
