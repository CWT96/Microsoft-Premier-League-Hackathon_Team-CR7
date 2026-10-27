"""Highlights timeline: the ~3-minute cut of the match as a fixed list of timed cues.

``build_timeline`` turns ``SCRIPT`` (Plays and Pauses) into cues the session plays at 1x.
Cue ``data`` is JSON-ready: the session sends it verbatim (except ``call.lock``).
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.core.types import Match, MatchEvent
from backend.demo.calls import CALL_WINDOW_S, ON_BALL, Call, find_moments, select_calls
from backend.demo.names import TEAMS, clock_label, display_name, fixed_xy

JUMP_CARD_S = 2.0
OPTIONS = ["PASS", "SHOOT", "DRIBBLE"]
EVENT_TYPES = ON_BALL | {"goal", "save", "kickoff"}
HOME, AWAY = "NBR", "CAL"


@dataclass(frozen=True)
class Play:
    half: int
    start_t: float
    end_t: float
    title: str


@dataclass(frozen=True)
class Pause:
    kind: str  # "half_time" | "full_time"
    seconds: float


@dataclass(frozen=True)
class Cue:
    at: float  # real seconds from demo start
    type: str
    data: dict


@dataclass(frozen=True)
class Timeline:
    cues: tuple[Cue, ...]
    calls: dict[str, Call]
    duration: float


SCRIPT: tuple[Play | Pause, ...] = (
    Play(1, 0, 8, "Kickoff"), Play(1, 88, 115, "Opening minutes"),
    Play(1, 1400, 1440, "Saka's goal"), Play(1, 2848, 2857, "Half time"),
    Pause("half_time", 25), Play(2, 3595, 3620, "Chelsea hit back"),
    Play(2, 4775, 4797, "The winner"), Play(2, 5655, 5668, "Full time"),
    Pause("full_time", 0),
)

# Order of cues that share the same `at`.
_RANK = {"demo.jump": 0, "demo.half_time": 1, "demo.full_time": 1, "demo.clock": 2,
         "call.lock": 3, "call.open": 4, "demo.event": 5, "demo.goal": 6}


def _score(goals: list[MatchEvent], half: int, t: float) -> list[int]:
    """[home, away] goals scored at or before (half, t)."""
    done = [g for g in goals if (g.half, g.t) <= (half, t)]
    return [sum(g.team == HOME for g in done), sum(g.team == AWAY for g in done)]


def _event_data(e: MatchEvent) -> dict:
    """Wire data of a ``demo.event`` cue."""
    xy, end_xy = fixed_xy(e.team, e.start), fixed_xy(e.team, e.end)
    return {"type": e.type, "team": TEAMS[e.team]["code"], "player": display_name(e.player_id),
            "xy": list(xy) if xy else None, "end_xy": list(end_xy) if end_xy else None}


def _play_cues(match: Match, play: Play, at: float, goals: list[MatchEvent],
               calls: list[Call]) -> list[Cue]:
    """Cues of one Play, with the play starting at real time ``at``."""
    cues: list[Cue] = []
    for k in range(int(-(-(play.end_t - play.start_t) // 1))):
        t = play.start_t + k
        cues.append(Cue(at + k, "demo.clock", {
            "half": play.half, "clock": clock_label(play.half, t),
            "score": _score(goals, play.half, t)}))
    for e in match.events:
        if e.type not in EVENT_TYPES or e.half != play.half or not play.start_t <= e.t < play.end_t:
            continue
        when = at + (e.t - play.start_t)
        cues.append(Cue(when, "demo.event", _event_data(e)))
        if e.type == "goal":
            cues.append(Cue(when, "demo.goal", {
                "team": TEAMS[e.team]["code"], "player": display_name(e.player_id),
                "clock": clock_label(e.half, e.t), "score": _score(goals, e.half, e.t)}))
    prev_t = play.start_t
    for c in calls:
        player = display_name(c.player_id)
        lock_real = at + (c.lock_t - play.start_t)
        window = min(CALL_WINDOW_S, c.lock_t - prev_t)
        cues.append(Cue(lock_real - window, "call.open", {
            "call_id": c.call_id, "player": player, "team": TEAMS[c.team]["code"],
            "prompt": f"{player} on the ball. What's the call?", "options": list(OPTIONS)}))
        cues.append(Cue(lock_real, "call.lock", {"call_id": c.call_id}))
        prev_t = c.lock_t
    return cues


def build_timeline(match: Match, script: tuple = SCRIPT) -> Timeline:
    """Walk ``script`` with a real-time cursor and emit every cue, sorted by (at, rank)."""
    goals = sorted((e for e in match.events if e.type == "goal"), key=lambda e: (e.half, e.t))
    moments = find_moments(match)
    cues: list[Cue] = []
    calls: dict[str, Call] = {}
    cursor, first, last = 0.0, True, (1, 0.0)
    for step in script:
        if isinstance(step, Pause):
            score = _score(goals, *last)
            cues.append(Cue(cursor, f"demo.{step.kind}", {"score": score, "seconds": step.seconds}))
            cursor += step.seconds
            continue
        if not first:
            cues.append(Cue(cursor, "demo.jump", {
                "title": step.title, "clock": clock_label(step.half, step.start_t)}))
            cursor += JUMP_CARD_S
        first, last = False, (step.half, step.end_t)
        chosen = select_calls(moments, step.half, step.start_t, step.end_t)
        calls.update({c.call_id: c for c in chosen})
        cues.extend(_play_cues(match, step, cursor, goals, chosen))
        cursor += step.end_t - step.start_t
    cues.sort(key=lambda c: (c.at, _RANK[c.type]))
    return Timeline(tuple(cues), calls, cursor)
