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
    """Select enemy kills, complete won 1v2–5 clutches, and kill-to-defuse plays."""
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
        selected.append(interval)

    # Bridge the last preceding kill to the player's completed defuse, even across a long gap.
    for defuse in analysis.defuses:
        round_ = rounds.get(defuse.round_number)
        if (defuse.player_steam_id != steam_id or round_ is None
                or not round_.start_tick <= defuse.tick < round_.end_tick):
            continue
        preceding = [i for i, clip in enumerate(selected)
                     if clip.round_number == defuse.round_number and max(clip.kill_ticks) <= defuse.tick]
        if preceding:
            index = max(preceding, key=lambda i: max(selected[i].kill_ticks))
            clip = selected[index]
            end = min(round_.end_tick, defuse.tick + max(1, math.ceil(after * analysis.tickrate)))
            selected[index] = replace(clip, end_tick=max(clip.end_tick, end), reason="enemy_kills+defuse")

    for clutch in analysis.clutches:
        round_ = rounds.get(clutch.round_number)
        if (clutch.player_steam_id != steam_id or not clutch.won
                or not 2 <= clutch.opponent_count <= 5 or round_ is None
                or not round_.start_tick <= clutch.tick < round_.end_tick):
            continue
        outcome = round_.outcome_tick if round_.outcome_tick is not None else round_.end_tick
        if outcome < clutch.tick:
            continue
        selected.append(Interval(
            max(round_.start_tick, clutch.tick - math.ceil(before * analysis.tickrate)),
            min(round_.end_tick, outcome + max(1, math.ceil(after * analysis.tickrate))),
            round_.number, steam_id, "won_clutch", (),
        ))

    merged: list[Interval] = []
    for interval in sorted(selected, key=lambda i: (i.start_tick, i.end_tick)):
        if (merged and merged[-1].round_number == interval.round_number
                and interval.start_tick <= merged[-1].end_tick):
            previous = merged[-1]
            reasons = "+".join(sorted(set(previous.reason.split("+")) | set(interval.reason.split("+"))))
            merged[-1] = replace(previous, end_tick=max(previous.end_tick, interval.end_tick),
                                 reason=reasons, kill_ticks=tuple(sorted(previous.kill_ticks + interval.kill_ticks)))
        else:
            merged.append(interval)
    return merged
