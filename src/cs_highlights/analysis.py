"""Normalize CS Demo Manager's Match JSON, independently of highlight selection."""
from dataclasses import asdict, dataclass
import math

from .errors import HighlightsError


@dataclass(frozen=True)
class Player:
    steam_id: str
    name: str


@dataclass(frozen=True)
class Round:
    number: int
    start_tick: int
    end_tick: int
    outcome_tick: int | None = None


@dataclass(frozen=True)
class Clutch:
    tick: int
    round_number: int
    player_steam_id: str
    opponent_count: int
    won: bool


@dataclass(frozen=True)
class Defuse:
    tick: int
    round_number: int
    player_steam_id: str


@dataclass(frozen=True)
class Kill:
    tick: int
    round_number: int
    killer_steam_id: str
    victim_steam_id: str
    killer_side: int
    victim_side: int
    weapon: str
    headshot: bool
    through_smoke: bool = False
    penetrated_objects: int = 0
    victim_x: float | None = None
    victim_y: float | None = None
    victim_z: float | None = None
    through_smoke_available: bool = True
    penetrated_objects_available: bool = True


@dataclass(frozen=True)
class Analysis:
    checksum: str
    map_name: str
    date: str
    tickrate: float
    tick_count: int
    players: tuple[Player, ...]
    rounds: tuple[Round, ...]
    kills: tuple[Kill, ...]
    clutches: tuple[Clutch, ...] = ()
    defuses: tuple[Defuse, ...] = ()
    shots: tuple[dict, ...] = ()
    blinds: tuple[dict, ...] = ()
    shots_available: bool = True
    blinds_available: bool = True
    intro_start_tick: int | None = None
    intro_end_tick: int | None = None

    def to_dict(self, *, include_review: bool = False) -> dict:
        data = asdict(self)
        if not include_review:
            data.pop("shots")
            data.pop("blinds")
            for kill in data["kills"]:
                for field in ("through_smoke", "penetrated_objects", "victim_x", "victim_y", "victim_z",
                              "through_smoke_available", "penetrated_objects_available"):
                    kill.pop(field)
            data.pop("shots_available")
            data.pop("blinds_available")
            data.pop("intro_start_tick")
            data.pop("intro_end_tick")
        return {"schema_version": 2, **data}


def integer(value, field: str) -> int:
    if type(value) is not int:
        raise HighlightsError(f"JSON de CS Demo Manager incompatible: {field} debe ser entero.")
    return value


def text(value, field: str) -> str:
    if not isinstance(value, str):
        raise HighlightsError(f"JSON de CS Demo Manager incompatible: {field} debe ser texto.")
    return value


def normalize(raw: dict) -> Analysis:
    try:
        if raw["game"] not in ("cs2", "CS2"):
            raise HighlightsError("Solo se admiten demos de CS2.")
        rate = raw["tickrate"]
        if type(rate) not in (float, int) or not math.isfinite(rate) or rate <= 0:
            raise HighlightsError("Tickrate ausente o inválido; no se puede convertir segundos a ticks.")
        ticks = integer(raw["tickCount"], "tickCount")
        if ticks <= 0:
            raise HighlightsError("La demo no contiene ticks válidos.")
        players = tuple(Player(text(p["steamId"], "steamId"), text(p["name"], "name")) for p in raw["players"])
        rounds = []
        intro_range = None
        for r in raw["rounds"]:
            number = integer(r["number"], "round.number")
            if number <= 0:
                continue
            start = max(1, integer(r["startTick"], "startTick"),
                        integer(r["freezetimeEndTick"], "freezetimeEndTick"))
            if number == 1:
                intro_start = max(0, integer(r["startTick"], "startTick"))
                intro_end = integer(r["freezetimeEndTick"], "freezetimeEndTick")
                if intro_end > intro_start:
                    intro_range = (intro_start, intro_end)
            official = integer(r["endOfficiallyTick"], "endOfficiallyTick")
            # endTick is the outcome event; our interval end is exclusive.
            end = min(ticks, official if official > start else integer(r["endTick"], "endTick") + 1)
            if end <= start:
                raise HighlightsError(f"Límites inválidos en la ronda {number}.")
            rounds.append(Round(number, start, end, integer(r["endTick"], "endTick")))
        rounds.sort(key=lambda r: r.start_tick)
        if len({r.number for r in rounds}) != len(rounds):
            raise HighlightsError("El análisis contiene números de ronda duplicados.")
        for previous, following in zip(rounds, rounds[1:]):
            if previous.end_tick > following.start_tick:
                raise HighlightsError("El análisis contiene rondas solapadas.")
        if not rounds:
            raise HighlightsError("No hay rondas competitivas válidas en la demo.")
        kills = []
        for k in raw["kills"]:
            # Warmup events are also excluded by the round's playable tick range.
            if k.get("isWarmup") is True:
                continue
            coordinates = []
            for field in ("victimX", "victimY", "victimZ"):
                value = k.get(field)
                if type(value) not in (int, float) or not math.isfinite(value):
                    coordinates.append(None)
                else:
                    coordinates.append(float(value))
            through_smoke = k.get("isThroughSmoke", False)
            if type(through_smoke) is not bool:
                raise HighlightsError("JSON de CS Demo Manager incompatible: kill.isThroughSmoke debe ser booleano.")
            kills.append(Kill(
                integer(k["tick"], "kill.tick"), integer(k["roundNumber"], "roundNumber"),
                text(k["killerSteamId"], "killerSteamId"), text(k["victimSteamId"], "victimSteamId"),
                integer(k["killerSide"], "killerSide"), integer(k["victimSide"], "victimSide"),
                text(k["weaponName"], "weaponName"), bool(k["isHeadshot"]),
                through_smoke, integer(k.get("penetratedObjects", 0), "kill.penetratedObjects"),
                *coordinates, "isThroughSmoke" in k, "penetratedObjects" in k,
            ))
        clutches = []
        for c in raw.get("clutches", []):
            if type(c["won"]) is not bool:
                raise HighlightsError("JSON de CS Demo Manager incompatible: clutch.won debe ser booleano.")
            clutches.append(Clutch(integer(c["tick"], "clutch.tick"),
                                  integer(c["roundNumber"], "clutch.roundNumber"),
                                  text(c["clutcherSteamId"], "clutcherSteamId"),
                                  integer(c["opponentCount"], "opponentCount"), c["won"]))
        defuses = tuple(Defuse(integer(d["tick"], "defuse.tick"),
                              integer(d["roundNumber"], "defuse.roundNumber"),
                              text(d["defuserSteamId"], "defuserSteamId"))
                        for d in raw.get("bombsDefused", []))
        shots = tuple(dict(shot) for shot in raw.get("shots", []) if isinstance(shot, dict))
        blinds = tuple(dict(blind) for blind in raw.get("blinds", []) if isinstance(blind, dict))
        return Analysis(text(raw["checksum"], "checksum"), text(raw["mapName"], "mapName"),
                        text(raw["date"], "date"), float(rate), ticks, players, tuple(rounds),
                        tuple(sorted(kills, key=lambda k: (k.tick, k.victim_steam_id))),
                        tuple(clutches), defuses, shots, blinds,
                        isinstance(raw.get("shots"), list), isinstance(raw.get("blinds"), list),
                        *(intro_range or (None, None)))
    except (KeyError, TypeError) as exc:
        raise HighlightsError(f"Formato JSON de CS Demo Manager incompatible: {exc}") from exc
