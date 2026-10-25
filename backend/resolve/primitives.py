"""Pure-code settling rules for question windows. Never calls an LLM."""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from backend.bank.types import Window


@dataclass(frozen=True)
class Ctx:
    """Match context the filters need."""
    home: str
    away: str
    possession_team: str | None
    trailing_team: str | None
    favourite: str | None  # player_id of the user's player
    team: str | None  # params["team"], supplied by the caller
    gk_ids: dict[str, str] = field(default_factory=dict)
    favourite_position: str | None = None


def is_penalty(event: dict) -> bool:
    """A foul inside the penalty area (x <= 16.5, 13.84 <= y <= 54.16)."""
    start = event.get("start")
    if event.get("type") != "foul" or not start:
        return False
    return start[0] <= 16.5 and 13.84 <= start[1] <= 54.16


def _is_switch(event: dict) -> bool:
    start, end = event.get("start"), event.get("end")
    if event.get("type") != "pass" or not start or not end:
        return False
    return ((start[1] < 22.67 and end[1] > 45.33)
            or (end[1] < 22.67 and start[1] > 45.33))


def _as_list(v) -> list:
    return v if isinstance(v, list) else [v]


def _team_ok(event: dict, rule: str, ctx: Ctx) -> bool:
    team = event.get("team")
    if rule == "any":
        return True
    if rule == "home":
        return team == ctx.home
    if rule == "away":
        return team == ctx.away
    if rule == "possession":
        return ctx.possession_team is not None and team == ctx.possession_team
    if rule == "opponent":
        return ctx.possession_team is not None and team != ctx.possession_team
    if rule == "trailing":
        return ctx.trailing_team is not None and team == ctx.trailing_team
    if rule == "param":
        return ctx.team is not None and team == ctx.team
    if rule == "param_opp":
        return ctx.team is not None and team != ctx.team
    raise ValueError(f"unknown team filter: {rule}")


def _stat_ok(event: dict, stat: str) -> bool:
    typ, outcome = event.get("type"), event.get("outcome")
    if stat == "touch":
        return True
    if stat == "pass_complete":
        return typ == "pass" and outcome == "complete"
    if stat == "win_ball":
        return typ == "interception" or (typ == "tackle" and outcome == "won")
    if stat in ("save", "clearance", "shot", "goal"):
        return typ == stat
    raise ValueError(f"unknown stat: {stat}")


def event_matches(event: dict, flt: dict, ctx: Ctx) -> bool:
    """True when the event satisfies every key of the filter."""
    for key, val in flt.items():
        if key == "type":
            ok = event.get("type") in _as_list(val)
        elif key == "outcome":
            ok = event.get("outcome") in _as_list(val)
        elif key == "team":
            ok = _team_ok(event, val, ctx)
        elif key == "player":
            if val == "favourite":
                ok = ctx.favourite is not None and event.get("player_id") == ctx.favourite
            elif val == "team_gk":
                gk = ctx.gk_ids.get(ctx.possession_team) if ctx.possession_team else None
                ok = gk is not None and event.get("player_id") == gk
            else:
                raise ValueError(f"unknown player filter: {val}")
        elif key == "stat":
            ok = _stat_ok(event, val)
        elif key == "derived":
            if val == "penalty":
                ok = is_penalty(event)
            elif val == "switch":
                ok = _is_switch(event)
            else:
                raise ValueError(f"unknown derived filter: {val}")
        elif key == "end_x_gt":
            end = event.get("end")
            ok = bool(end) and end[0] > val
        else:
            raise ValueError(f"unknown filter key: {key}")
        if not ok:
            return False
    return True


def _score(events: list[dict], ctx: Ctx) -> dict[str, int]:
    score = {ctx.home: 0, ctx.away: 0}
    for e in events:
        if e.get("type") == "goal" and e.get("team") in score:
            score[e["team"]] += 1
    return score


def _effective_filter(spec: dict, flt: dict, ctx: Ctx) -> dict:
    sbp = spec.get("stat_by_position")
    if sbp:
        stat = sbp.get(ctx.favourite_position)
        if stat is None:
            raise ValueError(f"no stat for position {ctx.favourite_position!r}")
        return {**flt, "stat": stat}
    return flt


def _count_at_least(spec, win_events, closed, ctx, params):
    n = spec["n"]
    if n == "line":
        n = (params or {})["line"]
    flt = _effective_filter(spec, spec["filter"], ctx)
    count = sum(1 for e in win_events if event_matches(e, flt, ctx))
    yes, no = ("no", "yes") if spec.get("negate") else ("yes", "no")
    if count >= n:
        return yes
    return no if closed else None


def _compare_counts(spec, win_events, closed, ctx):
    if not closed:
        return None
    a = sum(1 for e in win_events if event_matches(e, spec["filter_a"], ctx))
    b = sum(1 for e in win_events if event_matches(e, spec["filter_b"], ctx))
    return "yes" if a > b else "no"


def _first_of(spec, win_events, closed, ctx):
    for e in win_events:
        if event_matches(e, spec["filter_a"], ctx):
            return spec["options"][0]
        if event_matches(e, spec["filter_b"], ctx):
            return spec["options"][1]
    return "void" if closed else None


def _final_state(spec, all_events, win_events, closed, ctx):
    pred = spec["predicate"]
    if pred == "equalise":
        score = {ctx.home: 0, ctx.away: 0}
        in_win = {id(e) for e in win_events}
        for e in all_events:
            if e.get("type") == "goal" and e.get("team") in score:
                score[e["team"]] += 1
                if id(e) in in_win and score[ctx.home] == score[ctx.away]:
                    return "yes"
        return "no" if closed else None
    score = _score(all_events, ctx)
    if pred == "both_score":
        if score[ctx.home] > 0 and score[ctx.away] > 0:
            return "yes"
        return "no" if closed else None
    if pred == "team_wins":
        if not closed:
            return None
        if ctx.team not in score:
            raise ValueError("team_wins needs ctx.team")
        other = ctx.away if ctx.team == ctx.home else ctx.home
        return "yes" if score[ctx.team] > score[other] else "no"
    raise ValueError(f"unknown predicate: {pred}")


def _test_event(test: dict, e: dict) -> str:
    if "complete" in test:
        return "yes" if e.get("outcome") == "complete" else "no"
    start, end = e.get("start"), e.get("end")
    if "forward" in test:
        return "yes" if start and end and end[0] > start[0] else "no"
    if "min_length_m" in test:
        if not start or not end:
            return "short"
        length = math.hypot(end[0] - start[0], end[1] - start[1])
        return "long" if length >= test["min_length_m"] else "short"
    if "dx_dead_zone_m" in test:
        if not start or not end:
            return "sideways"
        dx = end[0] - start[0]
        dead = test["dx_dead_zone_m"]
        return "forward" if dx > dead else "backward" if dx < -dead else "sideways"
    raise ValueError(f"unknown test: {test}")


def _next_matching(spec, win_events, closed, ctx):
    flt = _effective_filter(spec, spec["filter"], ctx)
    for e in win_events:
        if event_matches(e, flt, ctx):
            return _test_event(spec["test"], e)
    return spec["no_event"] if closed else None


PRIMITIVES = {
    "count_at_least": _count_at_least,
    "compare_counts": _compare_counts,
    "first_of": _first_of,
    "final_state": _final_state,
    "next_matching": _next_matching,
}


def _after_start(e: dict, window: Window) -> bool:
    """True if the event comes after the window start (by half and time when it has a half)."""
    if window.half is None and window.start_half is not None and e.get("half") is not None:
        return (e["half"], e["t"]) > (window.start_half, window.start_t)
    return e["t"] > window.start_t


def settle(spec: dict, events: list[dict], window: Window, ctx: Ctx,
           closed: bool, params: dict | None = None) -> str | None:
    """Return an option id, "void", or None while the question is still open."""
    # Order by (half, t): the clock restarts at 2700 in half 2, after half-1 added time.
    ordered = sorted(events, key=lambda e: (e.get("half") or 0, e["t"]))
    if window.half is not None:
        ordered = [e for e in ordered if e.get("half") == window.half]
    after_start = [e for e in ordered if _after_start(e, window)]
    win_events = [e for e in after_start
                  if window.end_t is None or e["t"] <= window.end_t]
    if window.end_t is not None and len(win_events) < len(after_start):
        closed = True
    if window.end_marker and any(e.get("type") == window.end_marker for e in after_start):
        closed = True
    prim = spec["primitive"]
    if prim not in PRIMITIVES:
        raise ValueError(f"unknown primitive: {prim}")
    if prim == "count_at_least":
        return _count_at_least(spec, win_events, closed, ctx, params)
    if prim == "compare_counts":
        return _compare_counts(spec, win_events, closed, ctx)
    if prim == "first_of":
        return _first_of(spec, win_events, closed, ctx)
    if prim == "final_state":
        return _final_state(spec, ordered, win_events, closed, ctx)
    return _next_matching(spec, win_events, closed, ctx)
