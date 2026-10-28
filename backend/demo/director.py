"""Director view data: how the app picks each call, on one side's highlights cut.

``build_director`` returns the JSON behind ``GET /demo/director``: the side's full timeline
(cues, wire format), every decision moment inside a Play with what the picker did with it
(``calls.explain_selection``, plus ``too_short`` for the fan's side's moments under
MIN_WINDOW_S; the other side's are ``other_team`` whatever their window), the call
lines, ball movement and attack spans. Picked moments are exactly the timeline's calls.
Real time ``at`` maps to match time as in ``script.build_timeline``: a Play's match second
``t`` plays at ``at_start + (t - start_t)``.
"""

from __future__ import annotations

from backend.agents.llm import LLMClient
from backend.core.types import Match, MatchEvent
from backend.demo.calls import (MIN_GAP_S, MIN_RESULT_S, MIN_WINDOW_S, ON_BALL, _PRIORITY, Call,
                                explain_selection, find_moments)
from backend.demo.names import TEAMS, clock_label, display_name, fixed_xy
from backend.demo.script import JUMP_CARD_S, SCRIPTS, Play, build_timeline

SMOOTH_S = 3  # movement: centred moving average over this many one-second samples
ATTACK_X = {"NBR": 70.0, "CAL": 35.0}  # final third in the fixed frame (NBR attacks to the right)
_SIDE = {v["code"]: k for k, v in TEAMS.items()}  # display code -> internal key (the same since the rename)


def _plays(script: tuple, cues: list) -> list[dict]:
    """Each Play with its real-time range: it starts as its jump card ends."""
    plays = [s for s in script if isinstance(s, Play)]
    jumps = [c for c in cues if c.type == "demo.jump"]
    out = []
    for p, j in zip(plays, jumps, strict=True):
        at = j.at + JUMP_CARD_S
        out.append({"title": p.title, "half": p.half, "start_t": p.start_t, "end_t": p.end_t,
                    "at_start": at, "at_end": at + (p.end_t - p.start_t)})
    return out


def _moment(c: Call, play: dict, status: str, reason: str, against: str | None) -> dict:
    at = play["at_start"] - play["start_t"]
    return {"call_id": c.call_id, "half": c.half, "open_t": c.open_t, "lock_t": c.lock_t,
            "at_open": round(at + c.open_t, 3), "at_lock": round(at + c.lock_t, 3),
            "clock_open": clock_label(c.half, c.open_t), "clock_lock": clock_label(c.half, c.lock_t),
            "player": display_name(c.player_id), "team": TEAMS[c.team]["code"],
            "answer": c.answer, "status": status, "reason": reason, "against": against}


def _moments(match_id: str, plays: list[dict], side: str) -> list[dict]:
    every = find_moments(match_id, min_window=0)
    longer = [c for c in every if c.lock_t - c.open_t >= MIN_WINDOW_S]  # == find_moments()
    out = []
    for p in plays:
        h, t0, t1 = p["half"], p["start_t"], p["end_t"]
        for c in every:
            w = c.lock_t - c.open_t
            if not (c.half == h and t0 <= c.open_t and c.lock_t <= t1 and w < MIN_WINDOW_S):
                continue
            if c.team != side:  # the other side first: too short only matters for the fan's team
                out.append(_moment(c, p, "other_team", "Not the fan's team", None))
            else:
                out.append(_moment(c, p, "too_short",
                                   f"Only {w:.1f} s to answer (needs {MIN_WINDOW_S:.0f} s)", None))
        for d in explain_selection(longer, h, t0, t1, side):
            out.append(_moment(d.call, p, d.status, d.reason, d.against))
    return sorted(out, key=lambda m: (m["at_open"], m["at_lock"]))


def _ball(events: list[MatchEvent], half: int) -> list[tuple[float, tuple[float, float]]]:
    """(match t, fixed-frame spot) of every located on-ball event of the half, in time order."""
    out = []
    for e in sorted((e for e in events if e.half == half and e.type in ON_BALL),
                    key=lambda e: e.t):
        xy = fixed_xy(e.team, e.start)
        if xy is not None:
            out.append((e.t, xy))
    return out


def _speed_in(ball: list, a: float, b: float) -> float:
    """Mean ball speed (m/s) over match time [a, b): each leg's distance / time, time-weighted."""
    total = 0.0
    for (t0, p0), (t1, p1) in zip(ball, ball[1:]):
        lo, hi = max(a, t0), min(b, t1)
        if hi > lo and t1 > t0:
            v = ((p1[0] - p0[0]) ** 2 + (p1[1] - p0[1]) ** 2) ** 0.5 / (t1 - t0)
            total += v * (hi - lo)
    return total / (b - a)


def _x_at(ball: list, t: float) -> float | None:
    """Ball x at match time ``t``, linear between on-ball events (None outside them)."""
    for (t0, p0), (t1, p1) in zip(ball, ball[1:]):
        if t0 <= t <= t1:
            return p0[0] if t1 == t0 else p0[0] + (p1[0] - p0[0]) * (t - t0) / (t1 - t0)
    return None


def _movement_attack(match: Match, plays: list[dict]) -> tuple[list[dict], list[dict]]:
    movement: list[dict] = []
    attack: list[dict] = []
    half = SMOOTH_S // 2
    for p in plays:
        ball = _ball(match.events, p["half"])
        n = int(-(-(p["end_t"] - p["start_t"]) // 1))
        raw = []
        for k in range(n):
            a = p["start_t"] + k
            raw.append(_speed_in(ball, a, min(a + 1, p["end_t"])))
        for k in range(n):
            win = raw[max(0, k - half):k + half + 1]
            movement.append({"at": p["at_start"] + k, "v": round(sum(win) / len(win), 2)})
        for k in range(n):
            x = _x_at(ball, p["start_t"] + k + 0.5)
            side = None if x is None else ("NBR" if x > ATTACK_X["NBR"]
                                           else "CAL" if x < ATTACK_X["CAL"] else None)
            if side is None:
                continue
            at0, at1 = p["at_start"] + k, min(p["at_start"] + k + 1, p["at_end"])
            last = attack[-1] if attack else None
            if last and last["team"] == side and abs(last["at_end"] - at0) < 1e-9:
                last["at_end"] = at1
            else:
                attack.append({"at_start": at0, "at_end": at1, "team": side})
    return movement, attack


def build_director(match: Match, team: str = "NBR", client: LLMClient | None = None) -> dict:
    """The director JSON for the fan's side ``team`` (NBR / CAL)."""
    tl = build_timeline(match, client=client, team=team)
    plays = _plays(SCRIPTS[team], list(tl.cues))
    movement, attack = _movement_attack(match, plays)
    return {
        "team": team,
        "teams": {"home": TEAMS["NBR"], "away": TEAMS["CAL"]},
        "duration": tl.duration,
        "plays": plays,
        "cues": [{"at": c.at, "type": c.type, "data": c.data} for c in tl.cues],
        "moments": _moments(match.match_id, plays, _SIDE[team]),
        "lines": {cid: {"facts": ln.facts, "setup": ln.setup, "explain": ln.explain, "ai": ln.ai}
                  for cid, ln in tl.lines.items()},
        "movement": movement,
        "attack": attack,
        "rules": {"min_window_s": MIN_WINDOW_S, "min_gap_s": MIN_GAP_S,
                  "min_result_s": MIN_RESULT_S, "priority": sorted(_PRIORITY, key=_PRIORITY.get)},
    }
