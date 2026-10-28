"""Estimated team shape: where all 22 players stand, from lineup positions and the ball.

The dataset has no player tracking, so each player gets a formation slot that is shifted
toward the ball. Positions are in the fixed pitch frame (home attacks left to right).
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from backend.demo.names import PLAYERS, TEAMS
from backend.ingest import statsbomb
from backend.ingest.loader import SOURCE_DIR

LENGTH, WIDTH = 105.0, 68.0
HOME = "NBR"

# Lineup position -> base (x, y) for a team attacking left to right.
SLOTS: dict[str, tuple[float, float]] = {
    "Goalkeeper": (5, 34),
    "Left Back": (24, 10), "Right Back": (24, 58),
    "Left Center Back": (17, 25), "Center Back": (16, 34), "Right Center Back": (17, 43),
    "Left Wing Back": (32, 8), "Right Wing Back": (32, 60),
    "Left Defensive Midfield": (32, 26), "Center Defensive Midfield": (32, 34),
    "Right Defensive Midfield": (32, 42),
    "Left Center Midfield": (40, 22), "Right Center Midfield": (40, 46),
    "Left Midfield": (44, 10), "Right Midfield": (44, 58),
    "Center Attacking Midfield": (50, 34),
    "Left Wing": (58, 10), "Right Wing": (58, 58),
    "Left Center Forward": (62, 28), "Center Forward": (64, 34), "Right Center Forward": (62, 40),
}
_GROUP_FALLBACK = {"GK": (5, 34), "DF": (20, 34), "MF": (40, 34), "FW": (62, 34)}


@dataclass(frozen=True)
class Slot:
    player_id: str
    team: str  # internal code (NBR / CAL)
    position: str
    half_from: int
    t_from: float
    half_to: int | None
    t_to: float | None
    jersey: int = 0


def _clock(text: str) -> float:
    """``MM:SS`` match clock to seconds (same minute clock as ``MatchEvent.t``)."""
    m, s = text.split(":")
    return int(m) * 60 + float(s)


def _fallback(position: str) -> tuple[float, float]:
    """Base slot of an unknown position name, by its group."""
    p = position.lower()
    group = ("GK" if "keeper" in p else "DF" if "back" in p
             else "FW" if "forward" in p or "wing" in p or "striker" in p else "MF")
    return _GROUP_FALLBACK[group]


def load_slots(match_id: str = "nbr-cal-seed42") -> list[Slot]:
    """Every lineup position stint of the match as a Slot."""
    _, hid = statsbomb.find_match(match_id, SOURCE_DIR)
    teams = json.loads((SOURCE_DIR / "extras" / "teams.json").read_text())
    codes = {t["team_id"]: t["code"] for t in teams}
    lineups = json.loads((SOURCE_DIR / "lineups" / f"{hid}.json").read_text())
    slots: list[Slot] = []
    for team in lineups:
        for p in team["lineup"]:
            for pos in p["positions"]:
                end = pos["to"]
                slots.append(Slot(
                    str(p["player_id"]), codes[team["team_id"]], pos["position"],
                    pos["from_period"], _clock(pos["from"]),
                    pos["to_period"] if end else None, _clock(end) if end else None,
                    p["jersey_number"]))
    return slots


def on_pitch(slots: list[Slot], half: int, t: float) -> list[Slot]:
    """Slots active at (half, t): from <= now < to, ``to=None`` meaning until the end."""
    now = (half, t)
    return [s for s in slots if (s.half_from, s.t_from) <= now
            and (s.half_to is None or now < (s.half_to, s.t_to))]


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def team_shape(slots_now: list[Slot], ball: tuple[float, float],
               holder_id: str | None) -> list[dict]:
    """Positions of the players on the pitch: ``{"id", "team", "num", "x", "y"}``."""
    out = []
    for s in slots_now:
        away = s.team != HOME
        bx, by = (LENGTH - ball[0], WIDTH - ball[1]) if away else ball
        shift_x, shift_y = (bx - 52.5) * 0.45, (by - 34) * 0.25
        base_x, base_y = SLOTS.get(s.position) or _fallback(s.position)
        if s.position == "Goalkeeper":
            x, y = 5 + max(0.0, shift_x) * 0.15, 34 + (by - 34) * 0.1
        else:
            x, y = base_x + shift_x, 34 + (base_y - 34) * 0.9 + shift_y
            x = max(x, 7.0)  # outfield players never drop behind their own keeper
        x, y = _clamp(x, 1, 104), _clamp(y, 1, 67)
        if away:
            x, y = LENGTH - x, WIDTH - y
        if s.player_id == holder_id:
            x, y = _clamp(ball[0], 1, 104), _clamp(ball[1], 1, 67)
        num = PLAYERS[s.player_id][1] if s.player_id in PLAYERS else s.jersey
        out.append({"id": s.player_id, "team": TEAMS[s.team]["code"], "num": num,
                    "x": round(x, 1), "y": round(y, 1)})
    return out
