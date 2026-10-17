from dataclasses import replace
from pathlib import Path

from cs_highlights.config import Config

PLAYER = "76561198000000001"
ENEMY = "76561198000000002"


def config(root: Path, **changes) -> Config:
    result = Config(PLAYER, root / "data", root / "cs2", root / "settings.json",
                    width=800, height=600, fps=10, reserve_gib=0)
    (result.cs2_dir / "game/csgo").mkdir(parents=True, exist_ok=True)
    return replace(result, **changes)


def kill(tick, round_number=1, killer=PLAYER, victim=ENEMY, killer_side=3, victim_side=2):
    return {"tick": tick, "roundNumber": round_number, "killerSteamId": killer, "victimSteamId": victim,
            "killerSide": killer_side, "victimSide": victim_side, "weaponName": "ak47", "isHeadshot": True}


def raw_match(kills=None):
    return {
        "game": "cs2", "checksum": "source-checksum", "mapName": "de_mirage",
        "date": "2026-10-16T12:00:00Z", "tickrate": 64, "tickCount": 8000,
        "players": [{"steamId": PLAYER, "name": "Player"}, {"steamId": ENEMY, "name": "Enemy"}],
        "rounds": [
            {"number": 1, "startTick": 1, "freezetimeEndTick": 100, "endTick": 2000, "endOfficiallyTick": 2100},
            {"number": 2, "startTick": 2200, "freezetimeEndTick": 3000, "endTick": 6900, "endOfficiallyTick": 7000},
        ],
        "kills": kills if kills is not None else [kill(640), kill(800), kill(1800), kill(4000, 2, killer_side=2, victim_side=3)],
    }
