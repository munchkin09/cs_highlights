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

    def to_dict(self) -> dict:
        return {"schema_version": 2, **asdict(self)}


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
        for r in raw["rounds"]:
            number = integer(r["number"], "round.number")
            if number <= 0:
                continue
            start = max(1, integer(r["startTick"], "startTick"),
                        integer(r["freezetimeEndTick"], "freezetimeEndTick"))
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
            kills.append(Kill(
                integer(k["tick"], "kill.tick"), integer(k["roundNumber"], "roundNumber"),
                text(k["killerSteamId"], "killerSteamId"), text(k["victimSteamId"], "victimSteamId"),
                integer(k["killerSide"], "killerSide"), integer(k["victimSide"], "victimSide"),
                text(k["weaponName"], "weaponName"), bool(k["isHeadshot"]),
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
        return Analysis(text(raw["checksum"], "checksum"), text(raw["mapName"], "mapName"),
                        text(raw["date"], "date"), float(rate), ticks, players, tuple(rounds),
                        tuple(sorted(kills, key=lambda k: (k.tick, k.victim_steam_id))),
                        tuple(clutches), defuses)
    except (KeyError, TypeError) as exc:
        raise HighlightsError(f"Formato JSON de CS Demo Manager incompatible: {exc}") from exc
