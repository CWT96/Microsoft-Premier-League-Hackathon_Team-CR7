"""Highlights timeline: the ~90-second cut of the match as a fixed list of timed cues.

``build_timeline`` turns ``SCRIPT`` (Plays and Pauses) into cues the session plays at 1x.
Cue ``data`` is JSON-ready: the session sends it verbatim (except ``call.lock``).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from backend.agents.llm import LLMClient
from backend.core.types import Match, MatchEvent
from backend.demo.calls import ON_BALL, Call, find_moments, select_calls
from backend.demo.lines import CallLines, build_lines
from backend.demo.names import TEAMS, clock_label, display_name, fixed_xy
from backend.demo.shape import load_slots, on_pitch, team_shape

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
    """Cues plus what the session needs to settle forecasts (events, half ends, final score)."""

    cues: tuple[Cue, ...]
    calls: dict[str, Call]
    duration: float
    events: tuple[MatchEvent, ...] = ()
    half_ends: dict[int, float] = field(default_factory=dict)
    final_score: tuple[int, int] = (0, 0)
    lines: dict[str, CallLines] = field(default_factory=dict)  # setup / explain per call_id
    ai_lines: bool = False  # every line came from the LLM cache and passed the guards


SCRIPT: tuple[Play | Pause, ...] = (
    Play(1, 691, 716, "Arsenal on the attack"),
    Play(1, 1422, 1440, "Saka's goal"),
    Pause("half_time", 10),
    Play(2, 3600, 3616, "Chelsea hit back"),
    Play(2, 4780, 4796, "The winner"),
    Pause("full_time", 0),
)

# Order of cues that share the same `at`.
_RANK = {"demo.jump": 0, "demo.half_time": 1, "demo.full_time": 1, "demo.clock": 2,
         "call.lock": 3, "call.open": 4, "demo.event": 5, "demo.shape": 6, "demo.goal": 7}


def _score(goals: list[MatchEvent], half: int, t: float) -> list[int]:
    """[home, away] goals scored at or before (half, t)."""
    done = [g for g in goals if (g.half, g.t) <= (half, t)]
    return [sum(g.team == HOME for g in done), sum(g.team == AWAY for g in done)]


def _event_data(e: MatchEvent) -> dict:
    """Wire data of a ``demo.event`` cue."""
    xy, end_xy = fixed_xy(e.team, e.start), fixed_xy(e.team, e.end)
    return {"type": e.type, "team": TEAMS[e.team]["code"], "player": display_name(e.player_id),
            "xy": list(xy) if xy else None, "end_xy": list(end_xy) if end_xy else None}


_BALL_TYPES = {"pass", "carry", "shot", "kickoff"}
_CENTRE = (52.5, 34.0)


def _shape_cue(slots: list, at: float, half: int, t: float, ball: tuple[float, float],
               holder: str | None) -> Cue:
    """Shape cue; the wire drops ``id`` (team + num identify a player) to keep the payload small."""
    players = [{k: v for k, v in p.items() if k != "id"}
               for p in team_shape(on_pitch(slots, half, t), ball, holder)]
    return Cue(at, "demo.shape", {"players": players})


def _play_cues(match: Match, play: Play, at: float, goals: list[MatchEvent],
               calls: list[Call], lines: dict[str, CallLines]) -> list[Cue]:
    """Cues of one Play, with the play starting at real time ``at``."""
    cues: list[Cue] = []
    slots = load_slots()
    in_play = [e for e in match.events if e.type in EVENT_TYPES and e.half == play.half
               and play.start_t <= e.t < play.end_t]
    first = next((fixed_xy(e.team, e.start) for e in in_play
                  if e.type in _BALL_TYPES and e.start), None)
    cues.append(_shape_cue(slots, at, play.half, play.start_t, first or _CENTRE, None))
    for k in range(int(-(-(play.end_t - play.start_t) // 1))):
        t = play.start_t + k
        cues.append(Cue(at + k, "demo.clock", {
            "half": play.half, "clock": clock_label(play.half, t),
            "score": _score(goals, play.half, t), "t": float(t)}))
    for e in match.events:
        if e.type not in EVENT_TYPES or e.half != play.half or not play.start_t <= e.t < play.end_t:
            continue
        when = at + (e.t - play.start_t)
        data = _event_data(e)
        cues.append(Cue(when, "demo.event", data))
        if data["xy"] and e.type != "goal":  # the goal-mouth point follows its shot
            cues.append(_shape_cue(slots, when, e.half, e.t, tuple(data["xy"]), e.player_id))
        if e.type == "goal":
            cues.append(Cue(when, "demo.goal", {
                "team": TEAMS[e.team]["code"], "player": display_name(e.player_id),
                "clock": clock_label(e.half, e.t), "score": _score(goals, e.half, e.t)}))
    for c in calls:  # open on the pass, lock on the receiver's action (select_calls: no overlap)
        player = display_name(c.player_id)
        cues.append(Cue(at + (c.open_t - play.start_t), "call.open", {
            "call_id": c.call_id, "player": player, "team": TEAMS[c.team]["code"],
            "prompt": lines[c.call_id].setup, "options": list(OPTIONS)}))
        lock_at = at + (c.lock_t - play.start_t)
        cues.append(Cue(lock_at, "call.lock", {"call_id": c.call_id}))
        xy = fixed_xy(c.team, c.lock_xy)
        if c.answer == "DRIBBLE" and xy:  # ingest drops dribbles: show the raw one
            cues.append(Cue(lock_at, "demo.event", {
                "type": "dribble", "team": TEAMS[c.team]["code"], "player": player,
                "xy": list(xy), "end_xy": None}))
            cues.append(_shape_cue(slots, lock_at, c.half, c.lock_t, xy, c.player_id))
    return cues


def build_timeline(match: Match, script: tuple = SCRIPT, client: LLMClient | None = None
                   ) -> Timeline:
    """Walk ``script`` with a real-time cursor and emit every cue, sorted by (at, rank).

    Call lines come from ``client`` (default: the committed LLM cache, never the network).
    """
    goals = sorted((e for e in match.events if e.type == "goal"), key=lambda e: (e.half, e.t))
    moments = find_moments(match.match_id)
    cues: list[Cue] = []
    calls: dict[str, Call] = {}
    lines: dict[str, CallLines] = {}
    cursor, last = 0.0, (1, 0.0)
    for step in script:
        if isinstance(step, Pause):
            score = _score(goals, *last)
            cues.append(Cue(cursor, f"demo.{step.kind}", {"score": score, "seconds": step.seconds}))
            cursor += step.seconds
            continue
        cues.append(Cue(cursor, "demo.jump", {  # the first one is the intro card
            "title": step.title, "clock": clock_label(step.half, step.start_t)}))
        cursor += JUMP_CARD_S
        last = (step.half, step.end_t)
        chosen = select_calls(moments, step.half, step.start_t, step.end_t)
        calls.update({c.call_id: c for c in chosen})
        lines.update(build_lines(match, chosen, client))
        cues.extend(_play_cues(match, step, cursor, goals, chosen, lines))
        cursor += step.end_t - step.start_t
    cues.sort(key=lambda c: (c.at, _RANK[c.type]))
    final = (sum(g.team == HOME for g in goals), sum(g.team == AWAY for g in goals))
    return Timeline(tuple(cues), calls, cursor, tuple(match.events),
                    {1: match.first_half_end_t, 2: match.full_end_t}, final, lines,
                    bool(lines) and all(ln.ai for ln in lines.values()))
