"""The demo's room: three simulated friends, a simulated crowd, badges, the stamp and signup rules.

Pure functions of the Timeline (no I/O, no clock). Friends and the crowd are deterministic:
friend picks come from ``random.Random(f"{name}-{call_id}")`` (forecasts:
``f"{name}-f15-{half}-{t0}"``), the crowd from one ``random.Random("crowd")`` stream.
"""

from __future__ import annotations

import math
import random
import re
from dataclasses import dataclass
from typing import Any, Callable

from backend.demo.calls import Call
from backend.demo.forecasts import WINDOWS, outcome, score_final, score_window
from backend.demo.points import score_call
from backend.demo.script import OPTIONS, Timeline

CODE_CHARS = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CODE_LEN = 4
CROWD_SIZE = 1000
CROWD_SEED = "crowd"
CROWD_ACCURACY = (0.2, 0.8)
SIGNUP_BONUS = 500
MAX_EMAIL_CHARS = 254
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
ON_FIRE_FROM = 3
YOU = "You"

_F15 = next(d for d in WINDOWS if d.key == "f15")
_F15_OTHER = {"goal": "nogoal", "nogoal": "goal"}
_FINAL_PICKS = (("ars", 2, 1), ("draw", 1, 1), ("che", 1, 2))


@dataclass(frozen=True)
class Friend:
    name: str
    accuracy: float
    final: tuple[str, int, int]  # (result, home, away)
    saved: bool


FRIENDS: tuple[Friend, ...] = (
    Friend("Mia", 0.70, ("ars", 1, 0), True),
    Friend("Dan", 0.50, ("draw", 1, 1), True),
    Friend("Leo", 0.35, ("che", 1, 2), False),
)


def room_code(rng: random.Random | None = None) -> str:
    rng = rng or random.SystemRandom()
    return "".join(rng.choice(CODE_CHARS) for _ in range(CODE_LEN))


def valid_email(email: Any) -> bool:
    return isinstance(email, str) and len(email) <= MAX_EMAIL_CHARS and bool(EMAIL_RE.fullmatch(email))


# --- simulated players ---------------------------------------------------------


def play_starts(timeline: Timeline) -> list[tuple[int, float]]:
    """(half, t) of each Play's first ``demo.clock`` tick."""
    starts: list[tuple[int, float]] = []
    fresh = False
    for cue in timeline.cues:
        if cue.type == "demo.jump":
            fresh = True
        elif cue.type == "demo.clock" and fresh:
            starts.append((cue.data["half"], cue.data["t"]))
            fresh = False
    return starts


def _f15_outcomes(timeline: Timeline) -> list[tuple[int, float, str]]:
    """(half, t0, outcome) of an f15 pick at each Play's first tick with no f15 still pending.

    Same one-pending-window rule as the fan: a pick is still pending at a later tick of the
    same half before its end ``t1`` (half time settles everything of the first half).
    """
    out = []
    pending: tuple[int, float] | None = None  # (half, t1) of the last pick
    for half, t0 in play_starts(timeline):
        if pending is not None and pending[0] == half and pending[1] > t0:
            continue
        t1 = min(t0 + _F15.minutes * 60, timeline.half_ends.get(half, math.inf))
        pending = (half, t1)
        out.append((half, t0, outcome(timeline.events, "f15", half, t0, t1)))
    return out


def _ordered_calls(timeline: Timeline) -> list[Call]:
    return sorted(timeline.calls.values(), key=lambda c: (c.half, c.open_t))


def _pick(rng: random.Random, accuracy: float, right: str, options: list[str]) -> str:
    """``right`` with probability ``accuracy``, else a uniformly chosen wrong option."""
    if rng.random() < accuracy:
        return right
    return rng.choice([o for o in options if o != right])


def _simulate(timeline: Timeline, f15s: list[tuple[int, float, str]],
              call_pick: Callable[[Call], str], f15_pick: Callable[[int, float, str], str],
              final: tuple[str, int, int]) -> tuple[int, int]:
    """(half-time total, full-time total) of a player who answers every call."""
    ht = ft = streak = 0
    for c in _ordered_calls(timeline):
        s = score_call(call_pick(c), c.answer, streak)
        streak = s.streak
        ft += s.points
        ht += s.points if c.half == 1 else 0
    for half, t0, got in f15s:
        pts = score_window(_F15, f15_pick(half, t0, got), got)
        ft += pts
        ht += pts if half == 1 else 0
    ft += sum(score_final(*final, timeline.final_score))
    return ht, ft


def friend_totals(timeline: Timeline) -> list[dict]:
    """[{name, ht, ft, saved}] for Mia, Dan and Leo."""
    f15s = _f15_outcomes(timeline)
    rows = []
    for f in FRIENDS:
        def call_pick(c: Call, f: Friend = f) -> str:
            return _pick(random.Random(f"{f.name}-{c.call_id}"), f.accuracy, c.answer, OPTIONS)

        def f15_pick(half: int, t0: float, got: str, f: Friend = f) -> str:
            rng = random.Random(f"{f.name}-f15-{half}-{t0}")
            return _pick(rng, f.accuracy, got, [got, _F15_OTHER[got]])

        ht, ft = _simulate(timeline, f15s, call_pick, f15_pick, f.final)
        rows.append({"name": f.name, "ht": ht, "ft": ft, "saved": f.saved})
    return rows


_CROWD_CACHE: dict[int, tuple[Timeline, list[int]]] = {}


def crowd_totals(timeline: Timeline) -> list[int]:
    """Full-time totals of the 1000 simulated players (cached per Timeline object)."""
    hit = _CROWD_CACHE.get(id(timeline))
    if hit is not None and hit[0] is timeline:
        return hit[1]
    f15s = _f15_outcomes(timeline)
    rng = random.Random(CROWD_SEED)
    totals = []
    for _ in range(CROWD_SIZE):
        acc = rng.uniform(*CROWD_ACCURACY)
        final = rng.choice(_FINAL_PICKS)
        _, ft = _simulate(
            timeline, f15s,
            lambda c: _pick(rng, acc, c.answer, OPTIONS),
            lambda half, t0, got: _pick(rng, acc, got, [got, _F15_OTHER[got]]),
            final)
        totals.append(ft)
    _CROWD_CACHE.clear()  # one demo timeline per process; keep only the latest
    _CROWD_CACHE[id(timeline)] = (timeline, totals)
    return totals


def top_pct(you: int, crowd: list[int]) -> int:
    return max(1, math.ceil(100 * sum(t >= you for t in crowd) / len(crowd)))


# --- room board, badges, stamp -------------------------------------------------


def board(you: int, saved: bool, friends: list[dict], phase: str) -> list[dict]:
    """Players by points desc; the fan wins ties except at 0 (a fan who never played is last)."""
    players = [{"name": YOU, "pts": you, "saved": saved, "me": True}]
    players += [{"name": f["name"], "pts": f[phase], "saved": f["saved"], "me": False}
                for f in friends]
    return sorted(players, key=lambda p: (-p["pts"], (not p["me"]) if p["pts"] else p["me"]))


def rank_of(players: list[dict]) -> int:
    return next(i for i, p in enumerate(players, 1) if p["me"])


def good_eye(cards: list[dict]) -> str | None:
    """Name of the first backed player who scored (design: "Your pick Saka scored"), else None."""
    for card in cards:
        if any(row["what"] == "Goal" for row in card.get("stats", [])):
            return card["name"]
    return None


def badges(final_right: bool, best_streak: int, good_eye: str | None) -> list[dict]:
    out = []
    if final_right:
        out.append({"title": "ORACLE", "text": "Called the full-time result"})
    if best_streak >= ON_FIRE_FROM:
        out.append({"title": "ON FIRE", "text": f"{best_streak} calls in a row"})
    if good_eye:
        out.append({"title": "GOOD EYE", "text": f"Your pick {good_eye} scored"})
    return out


def stamp(rank: int, code: str, pct: int) -> str:
    return f"#{rank} IN ROOM {code} · TOP {pct}% TONIGHT"
