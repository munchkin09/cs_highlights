from dataclasses import asdict, dataclass
import hashlib
import json
import math
from pathlib import Path
import re
import tomllib

from .errors import HighlightsError
from . import platforms


@dataclass(frozen=True)
class Config:
    steam_id: str
    data_dir: Path
    cs2_dir: Path | None
    csdm_settings: Path
    csdm: str = "csdm"
    ffmpeg: str = "ffmpeg"
    ffprobe: str = "ffprobe"
    psql: str = "psql"
    before_seconds: float = 5.0
    after_seconds: float = 3.0
    death_before_seconds: float = 10.0
    death_after_seconds: float = 3.0
    intro_seconds: float = 5.0
    width: int = 1920
    height: int = 1080
    fps: int = 60
    crf: int = 20
    timeout_seconds: int = 7200
    reserve_gib: float = 2.0
    capture_batch_size: int = 4

    def output_settings(self, mode: str = "highlights") -> dict:
        values = asdict(self)
        if mode == "highlights":
            return {"pipeline_version": 3, **{key: values[key] for key in
                    ("steam_id", "before_seconds", "after_seconds", "intro_seconds",
                     "width", "height", "fps", "crf")}}
        if mode != "deaths":
            raise HighlightsError(f"Modo de salida desconocido: {mode}.")
        fields = ("steam_id", "death_before_seconds", "death_after_seconds", "intro_seconds",
                  "width", "height", "fps", "crf")
        return {"pipeline_version": 1, "mode": mode, "review_version": 3,
                "composition": "split-screen-radar-v1", "true_view": True,
                **{key: values[key] for key in fields}}

    @property
    def fingerprint(self) -> str:
        return self.fingerprint_for_mode("highlights")

    def fingerprint_for_mode(self, mode: str) -> str:
        return hashlib.sha256(json.dumps(self.output_settings(mode), sort_keys=True).encode()).hexdigest()[:16]

    @property
    def raw_dirs(self) -> tuple[Path, Path]:
        if self.cs2_dir is None:
            raise HighlightsError("Falta paths.cs2_dir para capturar vídeo.")
        root = self.cs2_dir / "game" / "csgo"
        return root / "movie", root / "csdm" / "movie"


def load_config(path: Path) -> Config:
    path = path.expanduser().resolve()
    try:
        with path.open("rb") as file:
            data = tomllib.load(file)
    except (OSError, ValueError) as exc:
        raise HighlightsError(f"No se puede leer {path}: {exc}. Copia config.example.toml y edítalo.") from exc
    sections = {
        "player": {"steam_id"}, "paths": {"data_dir", "cs2_dir", "csdm_settings"},
        "tools": {"csdm", "ffmpeg", "ffprobe", "psql"},
        "selection": {"before_seconds", "after_seconds"},
        "deaths": {"before_seconds", "after_seconds"},
        "intro": {"duration_seconds"},
        "video": {"width", "height", "fps", "crf"},
        "runtime": {"timeout_seconds", "reserve_gib", "capture_batch_size"},
    }
    flat = {}
    for section, values in data.items():
        if section not in sections or not isinstance(values, dict) or values.keys() - sections[section]:
            raise HighlightsError(f"Sección u opción desconocida en config: {section}")
        if section == "deaths":
            flat.update({f"death_{key}": value for key, value in values.items()})
        elif section == "intro":
            flat["intro_seconds"] = values["duration_seconds"]
        else:
            flat.update(values)
    steam_id = flat.get("steam_id")
    if not isinstance(steam_id, str) or not re.fullmatch(r"[0-9]{17}", steam_id):
        raise HighlightsError("player.steam_id debe ser un SteamID64 de 17 dígitos entre comillas.")
    for name in ("data_dir",):
        if name not in flat:
            raise HighlightsError(f"Falta paths.{name}.")
    flat.setdefault("cs2_dir", None)
    flat.setdefault("csdm_settings", str(platforms.csdm_settings_path()))
    for name in ("data_dir", "cs2_dir", "csdm_settings"):
        value = flat[name]
        if name == "cs2_dir" and value is None:
            continue
        if not isinstance(value, str) or not value.strip():
            raise HighlightsError(f"{name} debe ser una ruta no vacía.")
        value = Path(value).expanduser()
        flat[name] = (path.parent / value).resolve()
    for name in sections["tools"]:
        flat.setdefault(name, platforms.default_tool(name))
        if name in flat:
            value = flat[name]
            if not isinstance(value, str) or not value.strip() or "\x00" in value:
                raise HighlightsError(f"tools.{name} debe ser un ejecutable.")
            if "/" in value or "\\" in value:
                flat[name] = str((path.parent / Path(value).expanduser()).resolve())
    for name, maximum in (("before_seconds", 3600), ("after_seconds", 3600), ("reserve_gib", 1024)):
        if name in flat:
            value = flat[name]
            if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= maximum:
                raise HighlightsError(f"{name} debe ser un número finito entre 0 y {maximum}.")
    flat["intro_seconds"] = flat.get("intro_seconds", 5.0)
    if (type(flat["intro_seconds"]) not in (int, float) or not math.isfinite(flat["intro_seconds"])
            or not 0 <= flat["intro_seconds"] <= 30):
        raise HighlightsError("intro.duration_seconds debe ser un número finito entre 0 y 30.")
    flat["death_before_seconds"] = flat.get("death_before_seconds", 10.0)
    flat["death_after_seconds"] = flat.get("death_after_seconds", 3.0)
    for name, maximum in (("death_before_seconds", 3600), ("death_after_seconds", 3600)):
        value = flat[name]
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= maximum:
            raise HighlightsError(f"deaths.{name.removeprefix('death_')} debe ser un número finito entre 0 y {maximum}.")
    bounds = {"width": (800, 7680), "height": (600, 4320), "fps": (1, 240),
              "crf": (0, 51), "timeout_seconds": (1, 86400), "capture_batch_size": (1, 32)}
    for name, (low, high) in bounds.items():
        if name in flat and (type(flat[name]) is not int or not low <= flat[name] <= high):
            raise HighlightsError(f"{name} debe ser un entero entre {low} y {high}.")
    result = Config(**flat)
    if result.width % 2 or result.height % 2:
        raise HighlightsError("La resolución debe ser par para H.264/yuv420p.")
    return result


def create_config(path: Path) -> None:
    """Create an editable, platform-appropriate template without overwriting user settings."""
    path = path.expanduser().resolve()
    quote = lambda value: json.dumps(str(value), ensure_ascii=False)
    lines = ['[player]', '# Sustituye este ejemplo por tu SteamID64, siempre entre comillas.',
             'steam_id = "76561198000000000"', '', '[paths]', 'data_dir = "./data"']
    if platforms.supports_capture():
        from .environment import csdm_game_dir
        try:
            game = csdm_game_dir({})
        except HighlightsError:
            game = platforms.steam_roots()[0] / "steamapps/common/Counter-Strike Global Offensive"
        lines.extend(['# Revisa esta ruta y el mismo juego en Playback de CS Demo Manager.',
                      f'cs2_dir = {quote(game)}'])
    else:
        lines.append('# macOS: análisis y descarga; la captura de CS2 requiere Windows o Linux.')
    lines.extend([f'csdm_settings = {quote(platforms.csdm_settings_path())}', '', '[tools]'])
    lines.extend(f'{name} = {quote(platforms.default_tool(name))}' for name in ("csdm", "ffmpeg", "ffprobe", "psql"))
    lines.extend(['', '[selection]', 'before_seconds = 5.0', 'after_seconds = 3.0', '',
                  '[deaths]', 'before_seconds = 10.0', 'after_seconds = 3.0', '',
                  '[intro]', 'duration_seconds = 5.0', '',
                  '[video]', 'width = 1920', 'height = 1080', 'fps = 60', 'crf = 20', '',
                  '[runtime]', 'timeout_seconds = 7200', 'reserve_gib = 2.0', 'capture_batch_size = 4', ''])
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8") as output:
            output.write("\n".join(lines))
    except FileExistsError as exc:
        raise HighlightsError(f"Ya existe {path}; se conserva su configuración.") from exc
