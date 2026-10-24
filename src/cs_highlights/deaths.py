"""Death selection and evidence-based local review rules."""
from dataclasses import asdict, dataclass
import math

from .analysis import Analysis, Kill
from .errors import HighlightsError


@dataclass(frozen=True)
class DeathPlay:
    start_tick: int
    end_tick: int
    round_number: int
    death_tick: int
    victim_steam_id: str
    killer_steam_id: str
    kill: Kill
    victim_name: str = "Víctima"
    killer_name: str = "Ejecutor"

    def to_dict(self) -> dict:
        result = asdict(self)
        result.pop("kill")
        result["kill"] = asdict(self.kill)
        return result


def select_deaths(analysis: Analysis, steam_id: str, before: float, after: float) -> list[DeathPlay]:
    if steam_id not in {player.steam_id for player in analysis.players}:
        raise HighlightsError(f"El jugador {steam_id} no aparece en la demo.")
    if any(not math.isfinite(value) or value < 0 for value in (before, after)):
        raise HighlightsError("Los márgenes del modo deaths deben ser finitos y no negativos.")
    rounds = {round_.number: round_ for round_ in analysis.rounds}
    players = {player.steam_id for player in analysis.players}
    names = {player.steam_id: player.name for player in analysis.players}
    plays, seen = [], set()
    for kill in analysis.kills:
        round_ = rounds.get(kill.round_number)
        if (kill.victim_steam_id != steam_id or kill.killer_steam_id == steam_id
                or kill.killer_steam_id not in players
                or {kill.killer_side, kill.victim_side} != {2, 3}
                or round_ is None or not round_.start_tick <= kill.tick < round_.end_tick):
            continue
        key = (kill.tick, kill.round_number, kill.killer_steam_id, kill.victim_steam_id)
        if key in seen:
            continue
        seen.add(key)
        plays.append(DeathPlay(
            max(round_.start_tick, kill.tick - math.ceil(before * analysis.tickrate)),
            min(round_.end_tick, kill.tick + max(1, math.ceil(after * analysis.tickrate))),
            kill.round_number, kill.tick, steam_id, kill.killer_steam_id, kill,
            names.get(steam_id, steam_id), names.get(kill.killer_steam_id, kill.killer_steam_id),
        ))
    return plays


def diagnose(play: DeathPlay, analysis: Analysis, positions: list[dict] | None = None) -> dict:
    """Return observable facts separately from limited, non-causal hypotheses."""
    kill = play.kill
    facts = []
    not_evaluable = ["audibility", "line_of_sight", "enemy_voice_or_team_information"]
    if not kill.through_smoke_available:
        not_evaluable.append("kill_through_smoke")
    if not kill.penetrated_objects_available:
        not_evaluable.append("penetrated_surface")
    if kill.through_smoke:
        facts.append(_evidence("kill_through_smoke", kill.tick, "La eliminación atravesó humo según la demo."))
    if kill.penetrated_objects > 0:
        facts.append(_evidence("penetrated_surface", kill.tick,
                               f"El disparo mortal penetró {kill.penetrated_objects} superficie(s)."))
    blinds = [event for event in analysis.blinds
              if event.get("flashedSteamId") == play.victim_steam_id
              and event.get("tick", -1) <= play.death_tick
              and event.get("tick", -1) + event.get("duration", 0) * analysis.tickrate >= play.death_tick]
    if blinds:
        blind = max(blinds, key=lambda event: event["tick"])
        facts.append(_evidence("victim_blinded", blind["tick"], "La víctima tenía cegamiento activo."))
    elif not analysis.blinds_available:
        not_evaluable.append("victim_blinded")

    positions = positions or []
    victim = _nearest_position(positions, play.victim_steam_id, play.death_tick, alive=True)
    killer = _nearest_position(positions, play.killer_steam_id, play.death_tick, alive=True)
    if victim and killer and victim["is_alive"] and killer["is_alive"]:
        bearing = math.degrees(math.atan2(killer["y"] - victim["y"], killer["x"] - victim["x"]))
        angle = abs((bearing - victim["yaw"] + 180) % 360 - 180)
        if angle > 90:
            facts.append(_evidence("killer_behind_victim", play.death_tick,
                                   "El ejecutor estaba detrás de la orientación de la víctima; "
                                   "esto no confirma visibilidad."))
    else:
        not_evaluable.append("relative_orientation_missing_positions")

    own_shots = [shot for shot in analysis.shots
                 if shot.get("playerSteamId") == play.victim_steam_id
                 and play.start_tick <= shot.get("tick", -1) < play.death_tick]
    hypotheses = []
    if own_shots:
        shot = max(own_shots, key=lambda event: event["tick"])
        hypotheses.append(_evidence("own_shot_before_death", shot["tick"],
                                    "Disparaste antes de morir; podría haber revelado tu posición."))
    elif not analysis.shots_available:
        not_evaluable.append("own_shot_before_death")
    return {"schema_version": 1, "facts": facts, "hypotheses": hypotheses,
            "not_evaluable": not_evaluable}


def _evidence(rule: str, tick: int, text: str) -> dict:
    return {"rule": rule, "tick": tick, "text": text}


def _nearest_position(positions: list[dict], player_id: str, tick: int,
                      *, alive: bool = False) -> dict | None:
    candidates = [point for point in positions if point["player_steam_id"] == player_id
                  and (not alive or point["is_alive"])]
    if not candidates:
        return None
    point = min(candidates, key=lambda item: abs(item["tick"] - tick))
    return point if abs(point["tick"] - tick) <= 32 else None
