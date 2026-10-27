"""Decision moments for the demo: where the fan is asked PASS / SHOOT / DRIBBLE.

Read from the raw StatsBomb events (ingest drops dribbles and pass recipients). A moment opens on a
completed pass; the receiver's first pass / shot / dribble is the answer and locks the call.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from backend.ingest import statsbomb
from backend.ingest.loader import SOURCE_DIR

# Kept so the old script.py still imports until it is rewired.
ON_BALL = {"pass", "carry", "shot"}
CALL_WINDOW_S = 5.0

MIN_WINDOW_S = 3.0
MIN_GAP_S = 6.0

_WALK = {"Pass", "Carry", "Shot", "Dribble"}
_ANSWER = {"Pass": "PASS", "Shot": "SHOOT", "Dribble": "DRIBBLE"}
_PRIORITY = {"SHOOT": 0, "DRIBBLE": 1, "PASS": 2}


@dataclass(frozen=True)
class Call:
    call_id: str
    half: int
    open_t: float
    lock_t: float
    player_id: str
    team: str
    answer: str


def _t(e: dict) -> float:
    """Match clock in seconds, same clock as ``MatchEvent.t`` (second half continues from 2700)."""
    frac = float(e["timestamp"].split(":")[-1]) % 1
    return e["minute"] * 60 + e["second"] + frac


def find_moments(match_id: str = "nbr-cal-seed42") -> list[Call]:
    """Every answerable decision moment in the match, sorted by (half, open_t)."""
    _, hid = statsbomb.find_match(match_id, SOURCE_DIR)
    codes = {t["team_id"]: t["code"]
             for t in json.loads((SOURCE_DIR / "extras" / "teams.json").read_text())}
    raw = json.loads((SOURCE_DIR / "events" / f"{hid}.json").read_text())
    evs = sorted((e for e in raw if e["type"]["name"] in _WALK),
                 key=lambda e: (e["period"], _t(e), e["index"]))
    moments: list[Call] = []
    for i, e in enumerate(evs):
        if e["type"]["name"] != "Pass" or "outcome" in e["pass"]:
            continue
        rid = e["pass"].get("recipient", {}).get("id")
        if rid is None:
            continue
        for f in evs[i + 1:]:
            if f["period"] != e["period"] or f["player"]["id"] != rid:
                break
            answer = _ANSWER.get(f["type"]["name"])
            if answer is None:
                continue  # a carry: keep walking
            if _t(f) - _t(e) >= MIN_WINDOW_S:
                moments.append(Call("c-" + f["id"], e["period"], _t(e), _t(f), str(rid),
                                    codes[f["team"]["id"]], answer))
            break
    return sorted(moments, key=lambda c: (c.half, c.open_t))


def select_calls(moments: list[Call], half: int, start_t: float, end_t: float) -> list[Call]:
    """Calls for one play range: greedy by SHOOT > DRIBBLE > PASS then time, no overlap, gapped."""
    cands = sorted((c for c in moments
                    if c.half == half and start_t <= c.open_t and c.lock_t <= end_t),
                   key=lambda c: (_PRIORITY[c.answer], c.lock_t))
    accepted: list[Call] = []
    for c in cands:
        if all(abs(c.open_t - a.open_t) >= MIN_GAP_S
               and (c.open_t >= a.lock_t or a.open_t >= c.lock_t) for a in accepted):
            accepted.append(c)
    return sorted(accepted, key=lambda c: c.open_t)
