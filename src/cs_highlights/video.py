from fractions import Fraction
import json
import math
import os
from pathlib import Path
import shutil
import re

from .commands import Runner
from .config import Config
from .errors import HighlightsError


def check_space(config: Config, output: Path, duration: float) -> None:
    # 32-bit pixels, header/audio overhead, copying across filesystems, and a reserve.
    raw = math.ceil(duration * config.fps * config.width * config.height * 4 * 1.25)
    reserve = math.ceil(config.reserve_gib * 2**30)
    game = config.cs2_dir / "game/csgo"
    same_disk = game.stat().st_dev == output.stat().st_dev
    for path, required in ((game, raw * (2 if same_disk else 1) + reserve),
                           (output, raw + reserve)):
        free = shutil.disk_usage(path).free
        if free < required:
            raise HighlightsError(f"Espacio insuficiente en {path}: se estiman {required / 2**30:.1f} GiB "
                                  f"y hay {free / 2**30:.1f} GiB libres.")


def validate_video(path: Path, duration: float, config: Config, runner: Runner) -> dict:
    if not path.is_file() or path.stat().st_size == 0:
        raise HighlightsError(f"No hay vídeo válido en {path}.")
    result = runner.run([config.ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
                        timeout=60)
    try:
        data = json.loads(result.stdout)
        streams = data["streams"]
        video = next(s for s in streams if s.get("codec_type") == "video")
        audio = next(s for s in streams if s.get("codec_type") == "audio")
        actual = float(data["format"]["duration"])
        video_duration = float(video.get("duration", actual))
        audio_duration = float(audio.get("duration", actual))
        fps = float(Fraction(video["avg_frame_rate"]))
        tolerance = max(0.5, 2 / config.fps, duration * 0.01)
        valid = (video["codec_name"] == "h264" and audio["codec_name"] == "aac"
                 and video["width"] == config.width and video["height"] == config.height
                 and video.get("pix_fmt") == "yuv420p" and abs(fps - config.fps) < 0.01
                 and all(math.isfinite(d) and d > 0 for d in (actual, video_duration, audio_duration))
                 and abs(actual - duration) <= tolerance
                 and abs(video_duration - duration) <= tolerance
                 and abs(video_duration - audio_duration) <= max(0.5, 2 / config.fps)
                 and "mp4" in data["format"].get("format_name", ""))
        if not valid:
            raise ValueError("codecs, resolución, fps o duración inesperados")
        return {"duration": actual, "video_duration": video_duration, "audio_duration": audio_duration,
                "video_codec": video["codec_name"], "audio_codec": audio["codec_name"], "fps": fps,
                "width": video["width"], "height": video["height"]}
    except (ValueError, KeyError, TypeError, StopIteration, ZeroDivisionError) as exc:
        raise HighlightsError(f"El vídeo no pasa la validación de ffprobe: {path}: {exc}") from exc


def concatenate(clips: list[Path], output: Path, duration: float, config: Config, runner: Runner,
                *, clip_directory: str = "clips") -> dict:
    # Only generated basenames in our dedicated media directories reach FFmpeg's playlist.
    entries = []
    for clip in clips:
        valid_name = (clip.name.replace(".mp4", "").isdigit() if clip_directory == "clips"
                      else bool(re.fullmatch(r"[A-Za-z0-9_-]+\.mp4", clip.name)))
        if clip_directory not in {"clips", "segments"} or clip.parent != output.parent / clip_directory or not valid_name:
            raise HighlightsError("Ruta de clip inesperada al concatenar.")
        entries.append(f"file '{clip_directory}/{clip.name}'")
    playlist = output.parent / "concat.txt" if clip_directory == "clips" else output.with_suffix(".concat.txt")
    playlist.write_text("\n".join(entries) + "\n", encoding="utf-8")
    temporary = output.with_name(output.stem + ".partial.mp4")
    required = sum(path.stat().st_size for path in clips) + int(config.reserve_gib * 2**30)
    if shutil.disk_usage(output.parent).free < required:
        raise HighlightsError("No hay espacio para unir los clips.")
    runner.run([config.ffmpeg, "-nostdin", "-y", "-v", "error", "-f", "concat", "-safe", "1",
                "-i", str(playlist), "-map", "0:v:0", "-map", "0:a:0", "-c", "copy",
                "-movflags", "+faststart", str(temporary)], log=output.parent / "logs/concat.log",
               timeout=config.timeout_seconds)
    metadata = validate_video(temporary, duration, config, runner)
    os.replace(temporary, output)
    return metadata


def replace_audio_with_silence(path: Path, duration: float, config: Config, runner: Runner) -> None:
    """Keep an opening image sequence while ensuring it contributes no non-player audio."""
    temporary = path.with_name(path.stem + ".silent.partial.mp4")
    runner.run([config.ffmpeg, "-nostdin", "-y", "-v", "error", "-i", str(path),
                "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
                "-map", "0:v:0", "-map", "1:a:0", "-t", f"{duration:.6f}",
                "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest",
                "-movflags", "+faststart", str(temporary)], timeout=config.timeout_seconds)
    os.replace(temporary, path)
