"""Decision moments for the demo: where the fan is asked PASS / SHOOT / DRIBBLE.

Read from the raw StatsBomb events (ingest drops dribbles and pass recipients). A moment opens on a
completed pass; the receiver's first pass / shot / dribble is the answer and locks the call.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from backend.demo.names import display_name
from backend.ingest import statsbomb
from backend.ingest.loader import SOURCE_DIR

# Ingested event types that move the ball (script.py shows them as demo events).
ON_BALL = {"pass", "carry", "shot"}

MIN_WINDOW_S = 3.0
MIN_GAP_S = 6.0
MIN_RESULT_S = 3.0  # a call's result stays on screen this long before the next call opens

_WALK = {"Pass", "Carry", "Shot", "Dribble"}
_ANSWER = {"Pass": "PASS", "Shot": "SHOOT", "Dribble": "DRIBBLE"}
_PRIORITY = {"DRIBBLE": 0, "SHOOT": 1, "PASS": 2}  # rarest first, so the demo shows all three


@dataclass(frozen=True)
class Call:
    call_id: str
    half: int
    open_t: float
    lock_t: float
    player_id: str
    team: str
    answer: str
    lock_xy: tuple[float, float] | None = None  # answering event's location, 105 x 68, own frame
    open_xy: tuple[float, float] | None = None  # opening pass's end (where he gets it), same frame
    lock_outcome: str | None = None  # raw outcome name of the answering event ("Goal", "Blocked")


def _t(e: dict) -> float:
    """Match clock in seconds, same clock as ``MatchEvent.t`` (second half continues from 2700)."""
    frac = float(e["timestamp"].split(":")[-1]) % 1
    return e["minute"] * 60 + e["second"] + frac


def _xy(e: dict) -> tuple[float, float] | None:
    """Raw StatsBomb location (120 x 80) in the 105 x 68 frame of ``MatchEvent``."""
    loc = e.get("location")
    return (loc[0] * 105 / 120, loc[1] * 68 / 80) if loc else None


def _end_xy(e: dict) -> tuple[float, float] | None:
    """Raw pass ``end_location`` in the 105 x 68 frame."""
    loc = e.get("pass", {}).get("end_location")
    return (loc[0] * 105 / 120, loc[1] * 68 / 80) if loc else None


def _outcome_name(f: dict) -> str | None:
    """Raw outcome name of a Pass / Shot / Dribble (a completed pass has none)."""
    body = f.get(f["type"]["name"].lower(), {})
    return body.get("outcome", {}).get("name") if isinstance(body, dict) else None


@dataclass(frozen=True)
class Decision:
    """What the picker did with one moment: ``status`` picked / blocked / other_team.

    ``against`` is the call_id of the accepted call a blocked moment lost to.
    """

    call: Call
    status: str
    reason: str
    against: str | None = None


def find_moments(match_id: str = "nbr-cal-seed42", min_window: float = MIN_WINDOW_S) -> list[Call]:
    """Every decision moment with at least ``min_window`` s to answer, sorted by (half, open_t).

    The default keeps only answerable moments; the director view passes 0 to see them all.
    """
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
            if _t(f) - _t(e) >= min_window:
                moments.append(Call("c-" + f["id"], e["period"], _t(e), _t(f), str(rid),
                                    codes[f["team"]["id"]], answer, _xy(f), _end_xy(e),
                                    _outcome_name(f)))
            break
    return sorted(moments, key=lambda c: (c.half, c.open_t))


def _blocked_by(c: Call, a: Call) -> str | None:
    """Why accepted call ``a`` keeps ``c`` out, or None if they fit together."""
    dt = abs(c.open_t - a.open_t)
    if dt < MIN_GAP_S:
        return (f"Too close to {display_name(a.player_id)}'s call "
                f"({dt:.1f} s apart, needs {MIN_GAP_S:.0f} s)")
    if not (c.open_t >= a.lock_t + MIN_RESULT_S or a.open_t >= c.lock_t + MIN_RESULT_S):
        return (f"Would cover {display_name(a.player_id)}'s result "
                f"(needs {MIN_RESULT_S:.0f} s after it locks)")
    return None


def explain_selection(moments: list[Call], half: int, start_t: float, end_t: float,
                      team: str | None = None) -> list[Decision]:
    """The decision log of one play range, sorted by open_t: every moment inside it and why.

    Greedy by DRIBBLE > SHOOT > PASS then lock time. A moment of the other side (``team``,
    internal code) is ``other_team``; a candidate is ``blocked`` by the earliest-accepted call
    it conflicts with (gap rule checked first), else ``picked``. ``select_calls`` is the picked
    ones, so the log can never disagree with the app.
    """
    inside = [c for c in moments if c.half == half and start_t <= c.open_t and c.lock_t <= end_t]
    out = [Decision(c, "other_team", "Not the fan's team")
           for c in inside if team is not None and c.team != team]
    cands = sorted((c for c in inside if team is None or c.team == team),
                   key=lambda c: (_PRIORITY[c.answer], c.lock_t))
    accepted: list[Call] = []
    for c in cands:
        hit = next(((a, why) for a in accepted if (why := _blocked_by(c, a))), None)
        if hit:
            out.append(Decision(c, "blocked", hit[1], hit[0].call_id))
        else:
            accepted.append(c)
            out.append(Decision(c, "picked", "Picked"))
    return sorted(out, key=lambda d: (d.call.open_t, d.call.lock_t))


def select_calls(moments: list[Call], half: int, start_t: float, end_t: float,
                 team: str | None = None) -> list[Call]:
    """Calls for one play range: greedy by DRIBBLE > SHOOT > PASS then time.

    ``team`` (internal code) keeps only that side's moments: a fan is asked about their team.

    Accepted calls open at least MIN_GAP_S apart, never overlap, and leave MIN_RESULT_S between
    one call's lock and the next call's opening, so every result is seen before the next card.
    The picked moments of ``explain_selection``.
    """
    return sorted((d.call for d in explain_selection(moments, half, start_t, end_t, team)
                   if d.status == "picked"), key=lambda c: c.open_t)
