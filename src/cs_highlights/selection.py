"""Pure selection policy. A future agent can return the same Interval contract."""
from dataclasses import asdict, dataclass, replace
import math

from .analysis import Analysis
from .errors import HighlightsError


@dataclass(frozen=True)
class Interval:
    start_tick: int
    end_tick: int
    round_number: int
    player_steam_id: str
    reason: str
    kill_ticks: tuple[int, ...]

    def to_dict(self) -> dict:
        return asdict(self)


def select_kills(analysis: Analysis, steam_id: str, before: float, after: float) -> list[Interval]:
    if steam_id not in {player.steam_id for player in analysis.players}:
        raise HighlightsError(f"El jugador {steam_id} no aparece en la demo. Comprueba la cuenta de Steam.")
    if any(not math.isfinite(v) or v < 0 for v in (before, after)):
        raise HighlightsError("Los márgenes de selección deben ser finitos y no negativos.")
    rounds = {r.number: r for r in analysis.rounds}
    selected: list[Interval] = []
    seen = set()
    for kill in analysis.kills:
        round_ = rounds.get(kill.round_number)
        if (kill.killer_steam_id != steam_id or kill.victim_steam_id == steam_id
                or {kill.killer_side, kill.victim_side} != {2, 3}
                or round_ is None or not round_.start_tick <= kill.tick < round_.end_tick):
            continue
        key = (kill.tick, kill.round_number, kill.victim_steam_id)
        if key in seen:
            continue
        seen.add(key)
        start = max(round_.start_tick, kill.tick - math.ceil(before * analysis.tickrate))
        # End is exclusive; retain the event's tick even with a zero post-roll.
        end = min(round_.end_tick, kill.tick + max(1, math.ceil(after * analysis.tickrate)))
        interval = Interval(start, end, round_.number, steam_id, "enemy_kills", (kill.tick,))
        if selected and selected[-1].round_number == interval.round_number and start <= selected[-1].end_tick:
            previous = selected[-1]
            selected[-1] = replace(previous, end_tick=max(previous.end_tick, end),
                                   kill_ticks=previous.kill_ticks + (kill.tick,))
        else:
            selected.append(interval)
    return selected
