"""Map animation and synchronized side-by-side review rendering."""
from pathlib import Path
import math
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont

from .commands import Runner
from .config import Config
from .deaths import DeathPlay
from .errors import HighlightsError

VICTIM_COLOR = (55, 205, 240, 255)
KILLER_COLOR = (255, 143, 64, 255)


def _sample(points: list[dict], tick: float, max_gap: float) -> dict | None:
    if not points:
        return None
    low, high = 0, len(points)
    while low < high:
        middle = (low + high) // 2
        if points[middle]["tick"] < tick:
            low = middle + 1
        else:
            high = middle
    if low < len(points) and points[low]["tick"] == tick:
        return points[low]
    if low == 0:
        nearest = points[0]
        return nearest if nearest["tick"] - tick <= max_gap else None
    if low == len(points):
        nearest = points[-1]
        return nearest if tick - nearest["tick"] <= max_gap else None
    before, after = points[low - 1], points[low]
    if tick - before["tick"] > max_gap or after["tick"] - tick > max_gap:
        return None
    amount = (tick - before["tick"]) / (after["tick"] - before["tick"])
    result = {**before, "tick": tick}
    for key in ("x", "y", "z"):
        result[key] = before[key] + (after[key] - before[key]) * amount
    result["yaw"] = after["yaw"] if amount >= 0.5 else before["yaw"]
    result["is_alive"] = before["is_alive"] and after["is_alive"]
    return result


def _freeze_time(points: list[dict], start_tick: int, tickrate: float, fps: int) -> float | None:
    """Freeze a POV at its first recorded dead sample, including death before the clip."""
    if not points:
        return None
    first = _sample(points, start_tick, tickrate / 2)
    if first is not None and not first["is_alive"]:
        return 1 / fps
    deaths = [point["tick"] for point in points
              if point["tick"] > start_tick and not point["is_alive"]]
    return (deaths[0] - start_tick) / tickrate if deaths else None


def _map_xy(point: dict, metadata: dict, size: int) -> tuple[int, int]:
    ratio = size / metadata["radar_size"]
    x = (point["x"] - metadata["position_x"]) / metadata["scale"] * ratio
    y = (metadata["position_y"] - point["y"]) / metadata["scale"] * ratio
    return round(x), round(y)


def _draw_player(draw: ImageDraw.ImageDraw, point: dict, metadata: dict, size: int, color) -> None:
    x, y = _map_xy(point, metadata, size)
    if not (0 <= x < size and 0 <= y < size):
        return
    draw.line((x, y, x + math.cos(math.radians(point["yaw"])) * 18,
               y - math.sin(math.radians(point["yaw"])) * 18), fill=color, width=4)
    draw.ellipse((x - 7, y - 7, x + 7, y + 7), fill=color, outline=(255, 255, 255), width=2)


def _review_frame(play: DeathPlay, frame_index: int, frame_count: int, positions: list[dict],
                  metadata: dict, analysis: dict, review: dict, size: tuple[int, int], font) -> Image.Image:
    width, height = size
    image = Image.new("RGB", size, (15, 19, 26))
    draw = ImageDraw.Draw(image)
    radars = [metadata["radar"], metadata.get("lower_radar")]
    radars = [radar for radar in radars if radar]
    panel_width = round(width * 0.56)
    map_size = min(round(height * 0.90), round(panel_width / max(1, len(radars))))
    map_top = (height - map_size) // 2
    maps = []
    for index, radar in enumerate(radars):
        try:
            source = Image.open(BytesIO(radar)).convert("RGBA").resize((map_size, map_size), Image.Resampling.LANCZOS)
        except (OSError, ValueError) as exc:
            raise HighlightsError(f"No se pudo abrir el radar del mapa: {exc}") from exc
        x = int(18 + index * (map_size + 14) +
                (panel_width - 36 - len(radars) * map_size - (len(radars) - 1) * 14) // 2)
        image.paste(source, (x, map_top), source)
        draw.rectangle((x, map_top, x + map_size, map_top + 26), fill=(0, 0, 0))
        draw.text((x + 8, map_top + 4), "Planta inferior" if index else "Planta superior",
                  font=font, fill=(235, 238, 245))
        maps.append((x, map_top))

    tick = play.start_tick + (play.end_tick - play.start_tick) * frame_index / max(1, frame_count)
    max_gap = max(1, analysis["tickrate"] * 0.5)
    by_player: dict[str, list[dict]] = {play.victim_steam_id: [], play.killer_steam_id: []}
    for point in positions:
        if point["player_steam_id"] in by_player and point["start_tick"] <= point["tick"] <= tick:
            by_player[point["player_steam_id"]].append(point)

    for player_id, color in ((play.victim_steam_id, VICTIM_COLOR), (play.killer_steam_id, KILLER_COLOR)):
        samples = by_player[player_id]
        active = [sample for sample in samples if sample["is_alive"]]
        floor_tracks = [active] if len(radars) == 1 else [
            [p for p in active if p["z"] >= metadata["threshold_z"]],
            [p for p in active if p["z"] < metadata["threshold_z"]],
        ]
        for floor_index, track in enumerate(floor_tracks[:len(maps)]):
            origin_x, origin_y = maps[floor_index]
            segments, segment = [], []
            for point in track:
                if segment and point["tick"] - segment[-1]["tick"] > max_gap:
                    segments.append(segment)
                    segment = []
                segment.append(point)
            if segment:
                segments.append(segment)
            for points in segments:
                coordinates = []
                for point in points:
                    x, y = _map_xy(point, metadata, map_size)
                    coordinates.append((origin_x + x, origin_y + y))
                if len(coordinates) > 1:
                    draw.line(coordinates, fill=color[:3], width=4)
            current = _sample(samples, tick, max_gap)
            if current and current["is_alive"] and (len(radars) == 1 or
                    (current["z"] >= metadata["threshold_z"]) == (floor_index == 0)):
                px, py = _map_xy(current, metadata, map_size)
                draw.line((origin_x + px, origin_y + py,
                           origin_x + px + math.cos(math.radians(current["yaw"])) * 18,
                           origin_y + py - math.sin(math.radians(current["yaw"])) * 18), fill=color, width=4)
                draw.ellipse((origin_x + px - 7, origin_y + py - 7,
                              origin_x + px + 7, origin_y + py + 7), fill=color,
                             outline=(255, 255, 255), width=2)

    if play.kill.victim_x is not None and play.kill.victim_y is not None:
        floor_index = (0 if len(radars) == 1 or play.kill.victim_z is None
                       or play.kill.victim_z >= metadata["threshold_z"] else 1)
        if floor_index < len(maps):
            origin_x, origin_y = maps[floor_index]
            death_position = {"x": play.kill.victim_x, "y": play.kill.victim_y}
            marker_x, marker_y = _map_xy(death_position, metadata, map_size)
            marker_x, marker_y = origin_x + marker_x, origin_y + marker_y
            draw.line((marker_x - 12, marker_y - 12, marker_x + 12, marker_y + 12),
                     fill=(255, 70, 80), width=5)
            draw.line((marker_x - 12, marker_y + 12, marker_x + 12, marker_y - 12),
                     fill=(255, 70, 80), width=5)

    draw.line((panel_width, 14, panel_width, height - 14), fill=(70, 77, 91), width=2)
    text_x, text_y = panel_width + 28, 28
    draw.text((text_x, text_y), "EVIDENCIAS DE LA JUGADA", font=font, fill=(255, 255, 255))
    text_y += 42
    elapsed = (tick - play.death_tick) / analysis["tickrate"]
    draw.text((text_x, text_y), f"Tiempo respecto a la muerte: {elapsed:+.1f} s", font=font,
              fill=(205, 216, 231))
    text_y += 36
    draw.text((text_x, text_y), f"Víctima: {play.victim_name}", font=font, fill=VICTIM_COLOR)
    text_y += 32
    draw.text((text_x, text_y), f"Ejecutor: {play.killer_name}", font=font, fill=KILLER_COLOR)
    text_y += 46
    for heading, events, color in (("HECHOS", review.get("facts", []), (120, 226, 160)),
                                   ("HIPÓTESIS", review.get("hypotheses", []), (255, 207, 112))):
        if events and text_y < height - 100:
            draw.text((text_x, text_y), heading, font=font, fill=color)
            text_y += 34
            for event in events[:3]:
                if text_y >= height - 30:
                    break
                draw.text((text_x, text_y), f"{(event['tick'] - play.death_tick) / analysis['tickrate']:+.1f}s · "
                          f"{event['text'][:72]}", font=font, fill=(222, 226, 235))
                text_y += 30
    unavailable = [item.split(":", 1)[1] for item in review.get("not_evaluable", [])
                   if item.startswith("perspective_unavailable_at_start:")]
    if unavailable and text_y < height - 40:
        draw.text((text_x, text_y), "POV sin jugador vivo al inicio del intervalo", font=font,
                  fill=(255, 190, 120))
    return image


def render_map_frames(play: DeathPlay, positions: list[dict], metadata: dict,
                      analysis: dict, review: dict, destination: Path, config: Config) -> tuple[int, float]:
    destination.mkdir(parents=True, exist_ok=True)
    duration = (play.end_tick - play.start_tick) / analysis["tickrate"]
    count = max(1, math.ceil(duration * config.fps))
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 24)
    except OSError:
        font = ImageFont.load_default()
    for index in range(count):
        frame = _review_frame(play, index, count, positions, metadata, analysis, review,
                              (config.width, config.height // 2), font)
        frame.save(destination / f"{index:06d}.png", optimize=True)
    return count, duration


def compose(victim: Path, killer: Path, frame_dir: Path, output: Path, duration: float,
            config: Config, runner: Runner, *, victim_freeze: float | None = None,
            killer_freeze: float | None = None) -> dict:
    temporary = output.with_name(output.stem + ".partial.mp4")
    sequence = frame_dir / "%06d.png"
    victim_filter = _camera_filter(victim_freeze, duration, config)
    killer_filter = _camera_filter(killer_freeze, duration, config)
    graph = (f"[0:v]{victim_filter},fps={config.fps},scale={config.width // 2}:{config.height // 2}:force_original_aspect_ratio=decrease,"
             f"pad={config.width // 2}:{config.height // 2}:(ow-iw)/2:(oh-ih)/2,setsar=1[v];"
             f"[1:v]{killer_filter},fps={config.fps},scale={config.width // 2}:{config.height // 2}:force_original_aspect_ratio=decrease,"
             f"pad={config.width // 2}:{config.height // 2}:(ow-iw)/2:(oh-ih)/2,setsar=1[k];"
             f"[2:v]scale={config.width}:{config.height // 2},setsar=1[m];"
             "[v][k]hstack=inputs=2[top];[top][m]vstack=inputs=2[outv]")
    runner.run([config.ffmpeg, "-nostdin", "-y", "-v", "error", "-i", str(victim), "-i", str(killer),
                "-framerate", str(config.fps), "-i", str(sequence), "-filter_complex", graph,
                "-map", "[outv]", "-map", "1:a:0", "-t", f"{duration:.6f}", "-c:v", "libx264",
                "-pix_fmt", "yuv420p", "-crf", str(config.crf), "-c:a", "aac", "-b:a", "192k",
                "-movflags", "+faststart", str(temporary)], timeout=config.timeout_seconds)
    metadata = validate_video(temporary, duration, config, runner)
    temporary.replace(output)
    return metadata


def _camera_filter(freeze_at: float | None, duration: float, config: Config) -> str:
    if freeze_at is None or freeze_at <= 0 or freeze_at >= duration:
        return "null"
    tail = max(0, duration - freeze_at)
    return f"trim=duration={freeze_at:.6f},setpts=PTS-STARTPTS,tpad=stop_mode=clone:stop_duration={tail:.6f}"


def validate_video(path: Path, duration: float, config: Config, runner: Runner) -> dict:
    from .video import validate_video as validate
    return validate(path, duration, config, runner)
