"""Poisson and goal-grid pricing for question-bank templates."""
from __future__ import annotations

import math
from typing import Callable, Literal

from backend.bank.context import ctx_for
from backend.bank.types import MatchState, Player, Template
from backend.model.priors import (INSTANT_DEFAULTS, MATCH_RATES_90, PLAYER_NEXT_PASS,
                                  POSITION_RATES_90, TEAM_GOALS_90)
from backend.resolve.primitives import event_matches

HALF_END_S = {1: 2700.0, 2: 5400.0}
FULL_END_S = 5400.0
GRID_MAX = 10


def _pmf(mean: float, kmax: int) -> list[float]:
    """Poisson pmf for k = 0..kmax."""
    out = [math.exp(-mean)]
    for k in range(1, kmax + 1):
        out.append(out[-1] * mean / k)
    return out


def p_at_least(rate90: float, minutes: float, n: int) -> float:
    """P(Poisson count over the window >= n)."""
    if n <= 0:
        return 1.0
    mean = rate90 * max(minutes, 0.0) / 90.0
    return max(0.0, 1.0 - sum(_pmf(mean, n - 1)))


def p_first(rate_a90: float, rate_b90: float, minutes: float) -> dict[str, float]:
    """P(A scores first), P(B first), P(neither) within the window."""
    total = (rate_a90 + rate_b90) * max(minutes, 0.0) / 90.0
    any_event = 1.0 - math.exp(-total)
    if rate_a90 + rate_b90 <= 0:
        return {"a": 0.0, "b": 0.0, "none": 1.0}
    share = rate_a90 / (rate_a90 + rate_b90)
    return {"a": any_event * share, "b": any_event * (1 - share), "none": 1.0 - any_event}


def p_more(rate_a90: float, rate_b90: float, minutes: float) -> float:
    """P(count A > count B), summing k up to 30."""
    pa = _pmf(rate_a90 * minutes / 90.0, 30)
    pb = _pmf(rate_b90 * minutes / 90.0, 30)
    return sum(pa[i] * sum(pb[:i]) for i in range(1, 31))


def p_final(predicate: str, score: dict[str, int], team: str | None, other: str | None,
            minutes_left: float) -> float:
    """Probability of a final-state predicate given the score and time left."""
    mean = TEAM_GOALS_90 * max(minutes_left, 0.0) / 90.0
    if predicate == "equalise":
        deficit = max(score[other] - score[team], 1)
        return p_at_least(TEAM_GOALS_90, minutes_left, deficit)
    pmf = _pmf(mean, GRID_MAX)
    if predicate == "both_score":
        a, b = list(score.values())[:2]
        pa = 1.0 if a > 0 else 1.0 - pmf[0]
        pb = 1.0 if b > 0 else 1.0 - pmf[0]
        return pa * pb
    if predicate == "team_wins":
        mine, theirs = score[team], score[other]
        return sum(pmf[i] * pmf[j] for i in range(GRID_MAX + 1) for j in range(GRID_MAX + 1)
                   if mine + i > theirs + j)
    raise ValueError(f"unknown predicate: {predicate}")


_WINDOW_GRID_S = list(range(30, 601, 30)) + [900, 1200, 1500, 1800]


def tune_player(position: str, stat: str, mode: Literal["window_s", "line"],
                minutes_left: float) -> tuple[int, float]:
    """Pick the window (seconds) or line whose P(yes) is closest to 0.5."""
    rate = POSITION_RATES_90[position][stat]
    if mode == "window_s":
        cap = minutes_left * 60
        cands = [w for w in _WINDOW_GRID_S if w <= cap] or [max(int(cap), 1)]
        scored = [(w, p_at_least(rate, w / 60, 1)) for w in cands]
    else:
        scored = [(n, p_at_least(rate, minutes_left, n)) for n in range(1, 41)]
    return min(scored, key=lambda wp: abs(wp[1] - 0.5))


def _window_minutes(params: dict, state: MatchState) -> float:
    w = params.get("window")
    if w is None:
        return params["window_s"] / 60
    if w == "half":
        return (HALF_END_S[state.half] - state.t) / 60
    if w == "match":
        return (FULL_END_S - state.t) / 60
    return float(str(w).rstrip("m"))


def _window_start(template: Template, state: MatchState) -> tuple[float, int | None] | None:
    """(start time, half filter) for pre-counted events, or None for counts_from lock.

    Matches guards.window_for: kickoff counts both halves, half_start only this half.
    """
    if template.counts_from == "kickoff":
        return 0.0, None
    if template.counts_from == "half_start":
        return (0.0 if state.half == 1 else HALF_END_S[1]), state.half
    return None


def _already(template: Template, params: dict, state: MatchState,
             player: Player | None, flt: dict) -> int:
    window = _window_start(template, state)
    if window is None:
        return 0
    start, half = window
    ctx = ctx_for(template, params, state, player)
    return sum(1 for e in state.events
               if e["t"] > start and (half is None or e.get("half") == half)
               and event_matches(e, flt, ctx))


def _yes_no(template: Template, p_yes: float) -> dict[str, float]:
    if template.resolve.get("negate"):
        p_yes = 1.0 - p_yes
    return {"yes": p_yes, "no": 1.0 - p_yes}


def _n_needed(template: Template, params: dict) -> int:
    n = template.resolve["n"]
    return params["line"] if n == "line" else n


def _price_count(t: Template, params: dict, state: MatchState,
                 player: Player | None) -> dict[str, float]:
    rate = MATCH_RATES_90[t.resolve["rate"]]
    need = _n_needed(t, params) - _already(t, params, state, player, t.resolve["filter"])
    return _yes_no(t, p_at_least(rate, _window_minutes(params, state), need))


def _price_first(t: Template, params: dict, state: MatchState,
                 player: Player | None) -> dict[str, float]:
    half_rate = MATCH_RATES_90[t.resolve["rate"]] / 2
    r = p_first(half_rate, half_rate, _window_minutes(params, state))
    return {"home": r["a"], "away": r["b"], "none": r["none"]}


def _price_compare(t: Template, params: dict, state: MatchState,
                   player: Player | None) -> dict[str, float]:
    half_rate = MATCH_RATES_90[t.resolve["rate"]] / 2
    yes = p_more(half_rate, half_rate, _window_minutes(params, state))
    return {"yes": yes, "no": 1.0 - yes}


def _price_grid(t: Template, params: dict, state: MatchState,
                player: Player | None) -> dict[str, float]:
    team = params.get("team")
    other = None
    if team is not None:
        other = state.away if team == state.home else state.home
    yes = p_final(t.resolve["predicate"], state.score, team, other,
                  _window_minutes(params, state))
    return {"yes": yes, "no": 1.0 - yes}


def _price_player(t: Template, params: dict, state: MatchState,
                  player: Player | None) -> dict[str, float]:
    if player is None:
        raise ValueError(f"{t.id} needs a player")
    spec = t.resolve
    if spec["primitive"] == "next_matching":
        test = spec["test"]
        kind = "complete" if "complete" in test else "long"
        p = PLAYER_NEXT_PASS[kind][player.position]
        return {"yes": p, "no": 1 - p} if kind == "complete" else {"long": p, "short": 1 - p}
    flt = spec["filter"]
    stat = flt.get("stat") or spec["stat_by_position"][player.position]
    full_flt = {**flt, "stat": stat}
    need = _n_needed(t, params) - _already(t, params, state, player, full_flt)
    if "window_s" in params:
        minutes = params["window_s"] / 60
    else:
        minutes = (HALF_END_S[state.half] - state.t) / 60
    return _yes_no(t, p_at_least(POSITION_RATES_90[player.position][stat], minutes, need))


def _price_instant(t: Template, params: dict, state: MatchState,
                   player: Player | None) -> dict[str, float]:
    return dict(INSTANT_DEFAULTS[t.id])


PRICERS: dict[str, Callable[[Template, dict, MatchState, Player | None], dict[str, float]]] = {
    "poisson_count": _price_count,
    "poisson_first": _price_first,
    "poisson_compare": _price_compare,
    "goal_grid": _price_grid,
    "player": _price_player,
    "instant": _price_instant,
}
