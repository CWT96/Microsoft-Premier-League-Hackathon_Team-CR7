"""Pure rules for the demo's forecasts: windows, outcomes and points."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from backend.core.types import MatchEvent

FINAL_POINTS = 3000
EXACT_POINTS = 2000
FINAL_OPTIONS = (("ars", "ARSENAL"), ("draw", "DRAW"), ("che", "CHELSEA"))

_TEAM_TO_SIDE = {"NBR": "ars", "CAL": "che"}


@dataclass(frozen=True)
class ForecastDef:
    """One forecast window type; `options` are (option id, display label)."""

    key: str
    label: str
    minutes: int
    points: int
    question: str
    options: tuple[tuple[str, str], ...]


WINDOWS: tuple[ForecastDef, ...] = (
    ForecastDef("f5", "NEXT 5 MIN", 5, 500, "Will there be a corner?",
                (("yes", "YES"), ("no", "NO"))),
    ForecastDef("f10", "NEXT 10 MIN", 10, 1000, "Who takes the next shot?",
                (("ars", "ARSENAL"), ("che", "CHELSEA"))),
    ForecastDef("f15", "NEXT 15 MIN", 15, 1500, "Will anyone score?",
                (("goal", "GOAL"), ("nogoal", "NO GOAL"))),
)


def window(defn: ForecastDef, half: int, t0: float, half_end: float) -> tuple[float, float]:
    """Match-time span [t0, min(t0 + minutes*60, half_end)] of a forecast picked at t0."""
    return (t0, min(t0 + defn.minutes * 60, half_end))


def outcome(events: Sequence[MatchEvent], key: str, half: int, t0: float, t1: float) -> str:
    """Settle forecast `key` from events in the same half with t0 <= t <= t1."""
    inside = [e for e in events if e.half == half and t0 <= e.t <= t1]
    if key == "f5":
        return "yes" if any(e.type == "corner" for e in inside) else "no"
    if key == "f10":
        shots = sorted((e for e in inside if e.type == "shot"), key=lambda e: e.t)
        return _TEAM_TO_SIDE.get(shots[0].team, "none") if shots else "none"
    if key == "f15":
        return "goal" if any(e.type == "goal" for e in inside) else "nogoal"
    raise ValueError(f"unknown forecast key {key!r}")


def score_window(defn: ForecastDef, pick: str, outcome: str) -> int:
    return defn.points if pick == outcome else 0


def result_of(home: int, away: int) -> str:
    if home > away:
        return "ars"
    return "che" if away > home else "draw"


def score_final(pick_result: str, home: int, away: int,
                final: tuple[int, int]) -> tuple[int, int]:
    """(result points, exact points); exact only together with the right result."""
    if pick_result != result_of(*final):
        return (0, 0)
    return (FINAL_POINTS, EXACT_POINTS if (home, away) == tuple(final) else 0)
