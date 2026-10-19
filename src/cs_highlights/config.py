from dataclasses import asdict, dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tomllib

from .errors import HighlightsError


@dataclass(frozen=True)
class Config:
    steam_id: str
    data_dir: Path
    cs2_dir: Path
    csdm_settings: Path
    csdm: str = "csdm"
    ffmpeg: str = "ffmpeg"
    ffprobe: str = "ffprobe"
    psql: str = "psql"
    before_seconds: float = 5.0
    after_seconds: float = 3.0
    width: int = 1920
    height: int = 1080
    fps: int = 60
    crf: int = 20
    timeout_seconds: int = 7200
    reserve_gib: float = 2.0

    def output_settings(self) -> dict:
        fields = ("steam_id", "before_seconds", "after_seconds", "width", "height", "fps", "crf")
        values = asdict(self)
        return {"pipeline_version": 2, **{key: values[key] for key in fields}}

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(json.dumps(self.output_settings(), sort_keys=True).encode()).hexdigest()[:16]

    @property
    def raw_dirs(self) -> tuple[Path, Path]:
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
        "video": {"width", "height", "fps", "crf"}, "runtime": {"timeout_seconds", "reserve_gib"},
    }
    flat = {}
    for section, values in data.items():
        if section not in sections or not isinstance(values, dict) or values.keys() - sections[section]:
            raise HighlightsError(f"Sección u opción desconocida en config: {section}")
        flat.update(values)
    steam_id = flat.get("steam_id")
    if not isinstance(steam_id, str) or not re.fullmatch(r"[0-9]{17}", steam_id):
        raise HighlightsError("player.steam_id debe ser un SteamID64 de 17 dígitos entre comillas.")
    for name in ("data_dir", "cs2_dir"):
        if name not in flat:
            raise HighlightsError(f"Falta paths.{name}.")
    config_home = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    flat.setdefault("csdm_settings", str(config_home / "csdm" / "settings.json"))
    for name in ("data_dir", "cs2_dir", "csdm_settings"):
        value = flat[name]
        if not isinstance(value, str) or not value.strip():
            raise HighlightsError(f"{name} debe ser una ruta no vacía.")
        value = Path(value).expanduser()
        flat[name] = (path.parent / value).resolve()
    for name in sections["tools"]:
        if name in flat:
            value = flat[name]
            if not isinstance(value, str) or not value.strip() or "\x00" in value:
                raise HighlightsError(f"tools.{name} debe ser un ejecutable.")
            if "/" in value:
                flat[name] = str((path.parent / Path(value).expanduser()).resolve())
    for name, maximum in (("before_seconds", 3600), ("after_seconds", 3600), ("reserve_gib", 1024)):
        if name in flat:
            value = flat[name]
            if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= maximum:
                raise HighlightsError(f"{name} debe ser un número finito entre 0 y {maximum}.")
    bounds = {"width": (800, 7680), "height": (600, 4320), "fps": (1, 240),
              "crf": (0, 51), "timeout_seconds": (1, 86400)}
    for name, (low, high) in bounds.items():
        if name in flat and (type(flat[name]) is not int or not low <= flat[name] <= high):
            raise HighlightsError(f"{name} debe ser un entero entre {low} y {high}.")
    result = Config(**flat)
    if result.width % 2 or result.height % 2:
        raise HighlightsError("La resolución debe ser par para H.264/yuv420p.")
    return result
