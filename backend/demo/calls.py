"""Decision moments for the demo: where the fan is asked PASS / SHOOT / DRIBBLE.

A moment is a completed forward pass; the receiver's next on-ball actions decide the answer.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from backend.core.types import Match, MatchEvent

ON_BALL = {"pass", "carry", "shot"}
MIN_PASS_END_X = 60.0
DRIBBLE_CARRY_M = 8.0
MIN_GAP_S = 5.0
CALL_WINDOW_S = 5.0

_PRIORITY = {"SHOOT": 0, "DRIBBLE": 1, "PASS": 2}


@dataclass(frozen=True)
class Call:
    call_id: str
    half: int
    lock_t: float
    player_id: str
    team: str
    answer: str


def _answer(seq: list[MatchEvent]) -> str | None:
    """Right call for a receiver's on-ball sequence, or None if there is no decision."""
    if any(e.type == "shot" for e in seq):
        return "SHOOT"
    carried = sum(
        math.dist(e.start, e.end) for e in seq
        if e.type == "carry" and e.start is not None and e.end is not None
    )
    if carried >= DRIBBLE_CARRY_M:
        return "DRIBBLE"
    if any(e.type == "pass" for e in seq):
        return "PASS"
    return None


def find_moments(match: Match) -> list[Call]:
    """Every decision moment in the match, sorted by (half, lock_t)."""
    on_ball = [e for e in match.events if e.type in ON_BALL]
    moments: list[Call] = []
    for i, e in enumerate(on_ball):
        if e.type != "pass" or e.outcome != "complete":
            continue
        if e.end is None or e.end[0] < MIN_PASS_END_X or i + 1 >= len(on_ball):
            continue
        first = on_ball[i + 1]
        if first.team != e.team or first.player_id == e.player_id:
            continue
        seq = [first]
        for nxt in on_ball[i + 2:]:
            if nxt.player_id != first.player_id:
                break
            seq.append(nxt)
        answer = _answer(seq)
        if answer is not None:
            moments.append(Call("c-" + first.id, first.half, first.t,
                                first.player_id or "", first.team, answer))
    return sorted(moments, key=lambda c: (c.half, c.lock_t))


def select_calls(moments: list[Call], half: int, start_t: float, end_t: float) -> list[Call]:
    """Calls for one play range: greedy by SHOOT > DRIBBLE > PASS then time, MIN_GAP_S apart."""
    cands = sorted((c for c in moments if c.half == half and start_t <= c.lock_t <= end_t),
                   key=lambda c: (_PRIORITY[c.answer], c.lock_t))
    accepted: list[Call] = []
    for c in cands:
        if all(abs(c.lock_t - a.lock_t) >= MIN_GAP_S for a in accepted):
            accepted.append(c)
    return sorted(accepted, key=lambda c: c.lock_t)
