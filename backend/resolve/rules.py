"""rules.py — resolver rules for the 10 catalog templates of docs/pipeline.md.

Last Modified: 2026-10-24 22:00, first version.
Inputs:  none (rules read the event log through ``RuleContext``)
Outputs: registers the rules in ``backend.resolve.registry.RULES`` at import

Workflow:
  - Instant rules scan events after the lock inside a window.
  - Forecast rules count events once the engine says the half or match is over.
  - Player rules never void: no evidence means "no".
  - ``next_pass_dir`` voids when play stops first (foul, ball out, restart award, goal, injury,
    VAR check, half end), per pipeline.md [4]: the question is about open play.
  - Each template has a validator (params, roster) that ``Resolver.watch`` runs up front.

CAUTION: coordinates are per acting team (it attacks left to right), so a pass's dx is
forward for whichever team made it. A goal is the ``goal`` event; ``shot`` is not counted.
"""

from __future__ import annotations

from typing import Any

from backend.app.models import Question
from backend.core.constants import FINAL_THIRD_MIN_X, RESTART_TYPES
from backend.core.constants import NOMINAL_HALF_END_S
from backend.core.types import MatchEvent, Roster
from backend.resolve.registry import Key, RuleContext, Verdict, event_key, register

# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
#
# Constants
#
# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

DEAD_ZONE_M = 5.0
NEXT_PASS_WINDOW_S = 15.0
KEEP_BALL_WINDOW_S = 15.0
FINAL_THIRD_WINDOW_S = 20.0
SHOT_SOON_WINDOW_S = 60.0

# Events that make a team the holder of the ball (the team field is the actor).
POSSESSION_TYPES = frozenset({"pass", "carry", "shot", "tackle", "interception", "clearance",
                              "save", "kickoff"})
# Play halts with no restart implied: a possession question cannot be judged.
HARD_STOP_TYPES = frozenset({"injury", "var_check", "water_break_start", "half_end", "full_end"})
# Anything that ends the wait for the next pass.
PASS_STOP_TYPES = HARD_STOP_TYPES | RESTART_TYPES | {"foul", "out", "goal"}
NOT_A_TOUCH_TYPES = frozenset({"card", "injury", "substitution"})
PRE_RESTART_TYPES = frozenset({"out", "foul"})  # precede a restart award; not "the first event"
HALF_END_TYPES = frozenset({"half_end", "full_end"})

# player_stat_over: position -> (stat name, counts-as-stat predicate)
STAT_BY_POSITION = {
    "GK": ("saves", lambda e: e.type == "save"),
    "DF": ("clearances", lambda e: e.type == "clearance"),
    "MF": ("completed_passes", lambda e: e.type == "pass" and e.outcome == "complete"),
    "FW": ("shots", lambda e: e.type == "shot"),
}


# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
#
# Helpers
#
# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%


def _num(x: float) -> int | float:
    """Integer when whole after rounding to 0.1, else one decimal (for ``facts``)."""
    r = round(x, 1)
    return int(r) if r == int(r) else r


_MISSING = object()  # sentinel so a param of 0 is not "missing"


def _param(q: Question, name: str, default: Any = _MISSING) -> Any:
    if name in q.params:
        return q.params[name]
    if default is _MISSING:
        raise ValueError(f"question {q.qid!r} ({q.template_id}) needs params[{name!r}]")
    return default


def _void(at: float | None) -> Verdict:
    return Verdict(None, (), {}, at)


def _deadline(ctx: RuleContext, window_s: float) -> Key:
    return (ctx.lock[0], ctx.lock[1] + window_s)


def _window(ctx: RuleContext, window_s: float) -> list[MatchEvent]:
    """Events strictly after the lock and no later than the deadline."""
    dl = _deadline(ctx, window_s)
    return [e for e in ctx.after_lock() if event_key(e) <= dl]


def _elapsed(ctx: RuleContext, e: MatchEvent) -> int | float:
    return _num(e.t - ctx.lock[1])


def _timed_out(ctx: RuleContext, window_s: float) -> bool:
    return ctx.now >= _deadline(ctx, window_s)


def _holder(ctx: RuleContext, e: MatchEvent) -> str:
    """Team that holds the ball after an on-ball event (a lost tackle hands it over)."""
    if e.type == "tackle" and e.outcome == "lost":
        return ctx.roster.opponent(e.team)
    return e.team


def _possession_team(q: Question, ctx: RuleContext) -> str | None:
    """Team in question: ``params['team']``, else holder at the last on-ball event before lock."""
    if q.params.get("team"):
        return q.params["team"]
    for e in ctx.before_lock():
        if e.type in RESTART_TYPES:
            return e.team
        if e.type in POSSESSION_TYPES:
            return _holder(ctx, e)
    return None


def _possession_scan(q: Question, ctx: RuleContext, window_s: float):
    """Walk the window for the possession team. Yields (kind, event, team) with kind in
    "own" (team on the ball), "turnover", "goal_for", "goal_against", "stop".
    Yields nothing if the team is unknown."""
    team = _possession_team(q, ctx)
    if team is None:
        return
    for e in _window(ctx, window_s):
        if e.type in HARD_STOP_TYPES:
            yield "stop", e, team
            return
        if e.type == "goal":
            yield ("goal_for" if e.team == team else "goal_against"), e, team
            return
        if e.type in RESTART_TYPES:  # a restart for the same team is still kept
            if e.team != team:
                yield "turnover", e, team
                return
        elif e.type in POSSESSION_TYPES:
            if _holder(ctx, e) != team:
                yield "turnover", e, team
                return
            yield "own", e, team


def _no_known_team(q: Question, ctx: RuleContext) -> bool:
    return _possession_team(q, ctx) is None


def _closing_verdict(ctx: RuleContext, window_s: float, on_deadline: Verdict) -> Verdict | None:
    """Verdict once nothing happened in the window.

    Void if the window runs past the end of its half (or the half was cut short), the
    deadline result once the deadline has passed, else wait.
    """
    dl = _deadline(ctx, window_s)
    if ctx.now[0] > ctx.lock[0] and dl[1] > NOMINAL_HALF_END_S[ctx.lock[0]]:
        return _void(ctx.now[1])
    if ctx.now >= dl:
        return on_deadline
    if ctx.ended:
        return _void(ctx.now[1])
    return None


# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
#
# Instant rules
#
# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%


def next_pass_dir(q: Question, ctx: RuleContext) -> Verdict | None:
    """dx > +dead_zone forward, < -dead_zone backward, else sideways. Void if not offered."""
    dead_zone = float(q.params.get("dead_zone_m", DEAD_ZONE_M))
    for e in _window(ctx, NEXT_PASS_WINDOW_S):
        if e.type in PASS_STOP_TYPES:
            return _void(e.t)
        if e.type != "pass":
            continue
        if e.start is None or e.end is None:
            return _void(e.t)
        dx = e.end[0] - e.start[0]
        direction = "forward" if dx > dead_zone else "backward" if dx < -dead_zone else "sideways"
        if direction not in {o.id for o in q.options}:
            return _void(e.t)
        return Verdict(direction, (e.id,), {"dx_m": _num(dx)}, e.t)
    return _closing_verdict(ctx, NEXT_PASS_WINDOW_S, _void(_deadline(ctx, NEXT_PASS_WINDOW_S)[1]))


def keep_ball(q: Question, ctx: RuleContext) -> Verdict | None:
    """Yes if the team never loses the ball for 15 s."""
    if _no_known_team(q, ctx):
        return _void(ctx.now[1])
    for kind, e, _ in _possession_scan(q, ctx, KEEP_BALL_WINDOW_S):
        if kind == "stop":
            return _void(e.t)
        if kind in ("turnover", "goal_against"):
            return Verdict("no", (e.id,), {"seconds_kept": _elapsed(ctx, e)}, e.t)
        if kind == "goal_for":
            return Verdict("yes", (e.id,), {"seconds_kept": _elapsed(ctx, e)}, e.t)
    end = _deadline(ctx, KEEP_BALL_WINDOW_S)[1]
    kept = Verdict("yes", (), {"seconds_kept": _num(KEEP_BALL_WINDOW_S)}, end)
    return _closing_verdict(ctx, KEEP_BALL_WINDOW_S, kept)


def _x_points(e: MatchEvent) -> list[float]:
    """x positions where the ball was in the team's hands during this event."""
    pts = [e.start[0]] if e.start else []
    landed = e.end and (e.type == "carry" or (e.type == "pass" and e.outcome == "complete"))
    if landed:
        pts.append(e.end[0])
    return pts


def _is_attacking_restart(e: MatchEvent) -> bool:
    """A corner, or a free kick awarded beyond x=70, already puts the ball in the final third."""
    return e.type == "corner" or (
        e.type == "free_kick" and e.start is not None and e.start[0] > FINAL_THIRD_MIN_X)


def reach_final_third(q: Question, ctx: RuleContext) -> Verdict | None:
    """Yes if the team on the ball is beyond x=70 within 20 s, before losing it."""
    if _no_known_team(q, ctx):
        return _void(ctx.now[1])
    first = next((e for e in _window(ctx, FINAL_THIRD_WINDOW_S)
                  if e.type not in PRE_RESTART_TYPES), None)
    team = _possession_team(q, ctx)
    if first is not None and first.team == team and _is_attacking_restart(first):
        return Verdict("yes", (first.id,), {"seconds_to_reach": _elapsed(ctx, first)}, first.t)
    for kind, e, _ in _possession_scan(q, ctx, FINAL_THIRD_WINDOW_S):
        if kind == "stop":
            return _void(e.t)
        if kind in ("turnover", "goal_against"):
            return Verdict("no", (), {"window_s": _num(FINAL_THIRD_WINDOW_S)}, e.t)
        if kind == "goal_for":
            return Verdict("yes", (e.id,), {"seconds_to_reach": _elapsed(ctx, e)}, e.t)
        if any(x > FINAL_THIRD_MIN_X for x in _x_points(e)):
            return Verdict("yes", (e.id,), {"seconds_to_reach": _elapsed(ctx, e)}, e.t)
    end = _deadline(ctx, FINAL_THIRD_WINDOW_S)[1]
    missed = Verdict("no", (), {"window_s": _num(FINAL_THIRD_WINDOW_S)}, end)
    return _closing_verdict(ctx, FINAL_THIRD_WINDOW_S, missed)


def shot_soon(q: Question, ctx: RuleContext) -> Verdict | None:
    """Yes on any shot by either team within 60 s. Void if the half ends first."""
    for e in _window(ctx, SHOT_SOON_WINDOW_S):
        if e.type == "shot":
            return Verdict("yes", (e.id,), {"seconds_to_shot": _elapsed(ctx, e)}, e.t)
        if e.type in HALF_END_TYPES:
            return _void(e.t)
    end = _deadline(ctx, SHOT_SOON_WINDOW_S)[1]
    return _closing_verdict(ctx, SHOT_SOON_WINDOW_S,
                            Verdict("no", (), {"window_s": _num(SHOT_SOON_WINDOW_S)}, end))


# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
#
# Forecast rules (called only at HT / FT)
#
# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%


def _yes_no(flag: bool, evidence: tuple[str, ...], facts: dict[str, Any], ctx: RuleContext
            ) -> Verdict:
    return Verdict("yes" if flag else "no", evidence, facts, ctx.now[1])


def _goals(ctx: RuleContext) -> list[MatchEvent]:
    return [e for e in ctx.log if e.type == "goal"]


def goals_over(q: Question, ctx: RuleContext) -> Verdict | None:
    """Yes if total goals reach ``params['line']``."""
    if ctx.final is None:
        return None
    goals = _goals(ctx)
    line = _param(q, "line")
    return _yes_no(len(goals) >= line, tuple(e.id for e in goals),
                   {"goals": len(goals), "line": line}, ctx)


def team_wins(q: Question, ctx: RuleContext) -> Verdict | None:
    """Yes if ``params['team']`` has more goals than the opponent."""
    if ctx.final is None:
        return None
    team = _param(q, "team")
    goals = _goals(ctx)
    gf = sum(1 for e in goals if e.team == team)
    ga = len(goals) - gf
    return _yes_no(gf > ga, tuple(e.id for e in goals),
                   {"goals_for": gf, "goals_against": ga}, ctx)


def _second_half_any(event_type: str):
    def rule(q: Question, ctx: RuleContext) -> Verdict | None:
        if ctx.final is None:
            return None
        hits = [e for e in ctx.log if e.type == event_type and e.half == 2]
        return _yes_no(bool(hits), tuple(e.id for e in hits), {"count": len(hits)}, ctx)
    rule.__doc__ = f"Yes if any {event_type} event happens in the second half."
    return rule


card_second_half = _second_half_any("card")
second_half_goal = _second_half_any("goal")


# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
#
# Player rules (never void)
#
# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%


def player_stat_over(q: Question, ctx: RuleContext) -> Verdict | None:
    """Yes if the player's position stat reaches ``line`` in the half asked (HT=1st, FT=2nd)."""
    if ctx.final is None:
        return None
    pid = _param_player(q)
    stat, counts = STAT_BY_POSITION[ctx.roster.by_id()[pid].position]
    half = 1 if q.resolve_by == "HT" else 2
    hits = [e for e in ctx.log if e.player_id == pid and e.half == half and counts(e)]
    line = _param(q, "line")
    return _yes_no(len(hits) >= line, tuple(e.id for e in hits),
                   {"count": len(hits), "line": line, "stat": stat}, ctx)


def _param_player(q: Question) -> str:
    if not q.player_id:
        raise ValueError(f"question {q.qid!r} ({q.template_id}) needs player_id")
    return q.player_id


def player_touch_window(q: Question, ctx: RuleContext) -> Verdict | None:
    """Yes at the player's first event inside the window. Substituted off or window over: no."""
    pid = _param_player(q)
    window_s = float(_param(q, "window_s"))
    for e in _window(ctx, window_s):
        if e.player_id == pid:
            if e.type == "substitution":
                return Verdict("no", (e.id,), {"touches": 0, "window_s": _num(window_s)}, e.t)
            if e.type in NOT_A_TOUCH_TYPES:
                continue
            return Verdict("yes", (e.id,), {"touches": 1, "window_s": _num(window_s)}, e.t)
        if e.type in HALF_END_TYPES:
            return Verdict("no", (), {"touches": 0, "window_s": _num(window_s)}, e.t)
    facts = {"touches": 0, "window_s": _num(window_s)}
    if _timed_out(ctx, window_s):
        return Verdict("no", (), facts, _deadline(ctx, window_s)[1])
    if ctx.ended:
        return Verdict("no", (), facts, ctx.now[1])
    return None


# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
#
# Validators (run by Resolver.watch)
#
# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%


def _need_number(q: Question, name: str, positive: bool = False) -> None:
    v = _param(q, name)
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0 or (positive and v == 0):
        raise ValueError(f"question {q.qid!r}: params[{name!r}] must be a "
                         f"{'positive' if positive else 'non-negative'} number, got {v!r}")


def _need_team(q: Question, roster: Roster, required: bool) -> None:
    team = q.params.get("team") if not required else _param(q, "team")
    if team is not None and team not in (roster.home, roster.away):
        raise ValueError(f"question {q.qid!r}: unknown team {team!r}")


def _need_player(q: Question, roster: Roster) -> None:
    pid = _param_player(q)
    if pid not in roster.by_id():
        raise ValueError(f"question {q.qid!r}: unknown player {pid!r}")


def _validate_next_pass_dir(q: Question, roster: Roster) -> None:
    if "dead_zone_m" in q.params:
        _need_number(q, "dead_zone_m")


def _validate_possession(q: Question, roster: Roster) -> None:
    _need_team(q, roster, required=False)


def _validate_goals_over(q: Question, roster: Roster) -> None:
    _need_number(q, "line")


def _validate_team_wins(q: Question, roster: Roster) -> None:
    _need_team(q, roster, required=True)


def _validate_player_stat(q: Question, roster: Roster) -> None:
    _need_player(q, roster)
    if roster.by_id()[q.player_id].position not in STAT_BY_POSITION:
        raise ValueError(f"question {q.qid!r}: no stat for position")
    if q.resolve_by not in ("HT", "FT"):
        raise ValueError(f"question {q.qid!r}: resolve_by must be 'HT' or 'FT'")
    _need_number(q, "line")


def _validate_player_touch(q: Question, roster: Roster) -> None:
    _need_player(q, roster)
    _need_number(q, "window_s", positive=True)


# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
#
# Registration
#
# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

for _id, _rule, _check in (
    ("next_pass_dir", next_pass_dir, _validate_next_pass_dir),
    ("keep_ball", keep_ball, _validate_possession),
    ("reach_final_third", reach_final_third, _validate_possession),
    ("shot_soon", shot_soon, None),
    ("goals_over", goals_over, _validate_goals_over),
    ("team_wins", team_wins, _validate_team_wins),
    ("card_second_half", card_second_half, None),
    ("second_half_goal", second_half_goal, None),
    ("player_stat_over", player_stat_over, _validate_player_stat),
    ("player_touch_window", player_touch_window, _validate_player_touch),
):
    register(_id, _rule, _check)
