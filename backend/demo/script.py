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
    team: str = "ARS"  # the fan's side this cut was made for


SCRIPT: tuple[Play | Pause, ...] = (
    Play(1, 694, 716, "Arsenal on the attack"),
    Play(1, 1011, 1023, "Arsenal keep pushing"),
    Play(1, 1428, 1440, "Saka's goal"),
    Pause("half_time", 10),
    Play(2, 3608, 3616, "Chelsea hit back"),
    Play(2, 4422, 4433, "Arsenal go again"),
    Play(2, 4784, 4796, "The winner"),
    Pause("full_time", 0),
)

# A Chelsea fan's highlights: Chelsea on the ball for the calls, and every goal of the match.
SCRIPT_CHE = (
    Play(1, 179, 190, "Chelsea start fast"),
    Play(1, 328, 347, "Chelsea break"),
    Play(1, 1430, 1440, "Saka's goal"),
    Pause("half_time", 10),
    Play(2, 3604, 3616, "Chelsea hit back"),
    Play(2, 4372, 4389, "Chelsea push for more"),
    Play(2, 4786, 4796, "The winner"),
    Pause("full_time", 0),
)

# Calls only ever ask about the fan's own team: each side gets its own cut of the match.
SCRIPTS = {"ARS": SCRIPT, "CHE": SCRIPT_CHE}

# Order of cues that share the same `at`.
_RANK = {"demo.jump": 0, "demo.half_time": 1, "demo.full_time": 1, "demo.clock": 2,
         "call.lock": 3, "call.open": 4, "demo.event": 5, "demo.shape": 6, "demo.goal": 7}


def _score(goals: list[MatchEvent], half: int, t: float) -> list[int]:
    """[home, away] goals scored at or before (half, t)."""
    done = [g for g in goals if (g.half, g.t) <= (half, t)]
    return [sum(g.team == HOME for g in done), sum(g.team == AWAY for g in done)]


def _event_data(e: MatchEvent) -> dict:
    """Wire data of a ``demo.event`` cue (motion fields are added by ``_play_cues``)."""
    xy, end_xy = fixed_xy(e.team, e.start), fixed_xy(e.team, e.end)
    return {"type": e.type, "team": TEAMS[e.team]["code"], "player": display_name(e.player_id),
            "xy": list(xy) if xy else None, "end_xy": list(end_xy) if end_xy else None}


_BALL_TYPES = {"pass", "carry", "shot", "kickoff"}
_CENTRE = (52.5, 34.0)
PASS_SPEED = 18.0  # m/s, ball in the air or along the ground
SHOT_SPEED = 30.0  # m/s


@dataclass(frozen=True)
class _Beat:
    """One on-ball moment of a clip, in time order: a located event or a call's dribble."""

    t: float
    player_id: str | None
    team: str  # internal code
    data: dict  # demo.event wire data


def _shape_cue(slots: list, at: float, half: int, t: float, ball: tuple[float, float],
               holder: str | None, dur: float, waiting: tuple[str, list] | None = None) -> Cue:
    """Shape cue: where the 22 players will be ``dur`` seconds from now (0 = place them now).

    ``waiting`` (player id, spot) is the receiver of the pass made at that moment: he is already
    on his way to where the ball will land, so he never has to sprint to meet it.
    The wire drops ``id`` (team + num identify a player) to keep the payload small.
    """
    players = []
    for p in team_shape(on_pitch(slots, half, t), ball, holder):
        if waiting and p["id"] == waiting[0] and p["id"] != holder:
            p = {**p, "x": round(min(104.0, max(1.0, waiting[1][0])), 1),
                 "y": round(min(67.0, max(1.0, waiting[1][1])), 1)}
        players.append({k: v for k, v in p.items() if k != "id"})
    return Cue(at, "demo.shape", {"players": players, "dur": round(dur, 3),
                                  "ball": [round(ball[0], 1), round(ball[1], 1)],
                                  "holder": display_name(holder) if holder else None})


def _dist(a: list | tuple, b: list | tuple) -> float:
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5


def _play_cues(match: Match, play: Play, at: float, goals: list[MatchEvent],
               calls: list[Call], lines: dict[str, CallLines]) -> list[Cue]:
    """Cues of one Play, with the play starting at real time ``at``.

    Motion is choreographed so the pitch stays in step: each ``demo.event`` says how long the
    ball takes (``flight``) and how long until the next beat (``dur``); the ``demo.shape`` sent
    with it is where everyone stands at that next beat, reached in ``dur`` seconds (the pass
    receiver meets the ball after ``flight``). The first shape of a clip places the players at
    once (``dur`` 0) while the jump card still covers the pitch.
    """
    cues: list[Cue] = []
    slots = load_slots()
    in_play = sorted((e for e in match.events if e.type in EVENT_TYPES and e.half == play.half
                      and play.start_t <= e.t < play.end_t), key=lambda e: e.t)
    beats: list[_Beat] = []
    for e in in_play:
        data = _event_data(e)
        if e.type == "goal":
            when = at + (e.t - play.start_t)
            cues.append(Cue(when, "demo.event", {**data, "dur": 0, "flight": 0, "to": None}))
            cues.append(Cue(when, "demo.goal", {
                "team": TEAMS[e.team]["code"], "player": display_name(e.player_id),
                "clock": clock_label(e.half, e.t), "score": _score(goals, e.half, e.t)}))
            continue
        beats.append(_Beat(e.t, e.player_id, e.team, data))
    for c in calls:  # ingest drops dribbles: show the raw one at the call's lock
        xy = fixed_xy(c.team, c.lock_xy)
        if c.answer == "DRIBBLE" and xy:
            beats.append(_Beat(c.lock_t, c.player_id, c.team, {
                "type": "dribble", "team": TEAMS[c.team]["code"],
                "player": display_name(c.player_id), "xy": list(xy), "end_xy": None}))
    beats.sort(key=lambda b: b.t)
    located = [b for b in beats if b.data["xy"]]

    def waiting(k: int) -> tuple[str, list] | None:
        """(receiver, landing spot) of the pass made at beat ``k``, if it finds a teammate."""
        if k + 1 >= len(located) or located[k].data["type"] != "pass" or not located[k].data["end_xy"]:
            return None
        b, nxt = located[k], located[k + 1]
        return (nxt.player_id, b.data["end_xy"]) if nxt.team == b.team and nxt.player_id else None

    def target(k: int) -> tuple[tuple[float, float], str | None]:
        """Ball spot and holder at beat ``k`` (after the last beat: where its ball ends up)."""
        if k < len(located):
            b = located[k]
            return tuple(b.data["xy"]), b.player_id
        last = located[-1] if located else None
        if last is None:
            return _CENTRE, None
        end = last.data["end_xy"] or last.data["xy"]
        holder = last.player_id if last.data["type"] in ("carry", "dribble") else None
        return tuple(end), holder

    ball0, holder0 = target(0)
    # placed during the jump card, so the cut to the new clip never shows players teleporting
    cues.append(_shape_cue(slots, max(0.0, at - JUMP_CARD_S), play.half, play.start_t,
                           ball0, holder0, 0.0, waiting(0)))
    for k in range(int(-(-(play.end_t - play.start_t) // 1))):
        t = play.start_t + k
        cues.append(Cue(at + k, "demo.clock", {
            "half": play.half, "clock": clock_label(play.half, t),
            "score": _score(goals, play.half, t), "t": float(t)}))
    for b in beats:
        when = at + (b.t - play.start_t)
        if not b.data["xy"]:
            cues.append(Cue(when, "demo.event", {**b.data, "dur": 0, "flight": 0, "to": None}))
            continue
        k = located.index(b)
        nxt = located[k + 1] if k + 1 < len(located) else None
        dur = max(0.0, (nxt.t if nxt else play.end_t) - b.t)
        kind, start, end = b.data["type"], b.data["xy"], b.data["end_xy"]
        receiver = nxt.player_id if (kind == "pass" and nxt and nxt.team == b.team) else None
        if kind == "pass" and end:
            flight = min(dur, max(0.35, _dist(start, end) / PASS_SPEED))
        elif kind == "shot" and end:
            flight = min(dur, max(0.25, _dist(start, end) / SHOT_SPEED))
        elif kind == "carry" and end:
            flight = dur
        else:
            flight = 0.0
        cues.append(Cue(when, "demo.event", {
            **b.data, "dur": round(dur, 3), "flight": round(flight, 3),
            "to": display_name(receiver) if receiver else None}))
        ball, holder = target(k + 1)
        cues.append(_shape_cue(slots, when, play.half, nxt.t if nxt else play.end_t,
                               ball, holder, dur, waiting(k + 1)))
    for c in calls:  # open on the pass, lock on the receiver's action (select_calls: no overlap)
        player = display_name(c.player_id)
        cues.append(Cue(at + (c.open_t - play.start_t), "call.open", {
            "call_id": c.call_id, "player": player, "team": TEAMS[c.team]["code"],
            "prompt": lines[c.call_id].setup, "options": list(OPTIONS)}))
        cues.append(Cue(at + (c.lock_t - play.start_t), "call.lock", {"call_id": c.call_id}))
    return cues


def build_timeline(match: Match, script: tuple | None = None, client: LLMClient | None = None,
                   team: str = "ARS") -> Timeline:
    """Walk ``script`` with a real-time cursor and emit every cue, sorted by (at, rank).

    ``team`` (ARS / CHE) is the fan's side: its highlights cut (``SCRIPTS``) unless ``script`` is
    given, and calls on that side's players only. Call lines come from ``client`` (default: the
    committed LLM cache, never the network).
    """
    script = script if script is not None else SCRIPTS[team]
    side = HOME if team == "ARS" else AWAY
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
        chosen = select_calls(moments, step.half, step.start_t, step.end_t, side)
        calls.update({c.call_id: c for c in chosen})
        lines.update(build_lines(match, chosen, client))
        cues.extend(_play_cues(match, step, cursor, goals, chosen, lines))
        cursor += step.end_t - step.start_t
    cues.sort(key=lambda c: (c.at, _RANK[c.type]))
    final = (sum(g.team == HOME for g in goals), sum(g.team == AWAY for g in goals))
    return Timeline(tuple(cues), calls, cursor, tuple(match.events),
                    {1: match.first_half_end_t, 2: match.full_end_t}, final, lines,
                    bool(lines) and all(ln.ai for ln in lines.values()), team)
