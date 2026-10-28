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
    team: str = "NBR"  # the fan's side this cut was made for


# Clip titles never give away what happens next: the fan watches it as live.
SCRIPT: tuple[Play | Pause, ...] = (
    Play(1, 694, 716, "Northbridge on the attack"),
    Play(1, 1011, 1023, "Northbridge keep pushing"),
    Play(1, 1428, 1440, "Northbridge in the box"),
    Pause("half_time", 10),
    Play(2, 3608, 3616, "Calder on the break"),
    Play(2, 4422, 4433, "Northbridge go again"),
    Play(2, 4784, 4796, "Late pressure"),
    Pause("full_time", 0),
)

# A Calder fan's highlights: Calder on the ball for the calls, and every goal of the match.
SCRIPT_CHE = (
    Play(1, 179, 190, "Calder start fast"),
    Play(1, 328, 347, "Calder break"),
    Play(1, 1430, 1440, "Northbridge in the box"),
    Pause("half_time", 10),
    Play(2, 3604, 3616, "Calder press forward"),
    Play(2, 4372, 4389, "Calder push for more"),
    Play(2, 4786, 4796, "Late pressure"),
    Pause("full_time", 0),
)

# Calls only ever ask about the fan's own team: each side gets its own cut of the match.
SCRIPTS = {"NBR": SCRIPT, "CAL": SCRIPT_CHE}

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
            "xy": list(xy) if xy else None, "end_xy": list(end_xy) if end_xy else None,
            **({"goal": (e.outcome or "").lower() == "goal"} if e.type == "shot" else {})}


_BALL_TYPES = {"pass", "carry", "shot", "kickoff"}
_CENTRE = (52.5, 34.0)
PASS_SPEED = 18.0  # m/s, ball in the air or along the ground
DRIBBLE_LEAD = 1.5  # s: a take-on starts this long before the dribble's data moment
MIN_OPEN_S = 2.5  # s: a call card is up at least this long before it locks
RUN_IN = 7.5  # m/s: how fast a pass receiver runs to where the ball will land
SHOT_SPEED = 22.0  # m/s on average: ~35 m/s off the boot, as the client eases it


@dataclass(frozen=True)
class _Beat:
    """One on-ball moment of a clip, in time order: a located event or a call's dribble."""

    t: float
    player_id: str | None
    team: str  # internal code
    data: dict  # demo.event wire data


def _shape_cue(slots: list, at: float, half: int, t: float, ball: tuple[float, float],
               holder: str | None, dur: float, placed: dict | None = None) -> Cue:
    """Shape cue: where the 22 players will be ``dur`` seconds from now (0 = place them now).

    ``placed`` puts players off their formation spot: a pass receiver already on his way to
    where the ball lands, the defender a dribbler takes on, the keeper going for a shot.
    The wire drops ``id`` (team + num identify a player) to keep the payload small.
    """
    players = []
    for p in team_shape(on_pitch(slots, half, t), ball, holder):
        spot = (placed or {}).get(p["id"])
        if spot and p["id"] != holder:
            p = {**p, "x": round(min(104.0, max(1.0, spot[0])), 1),
                 "y": round(min(67.0, max(1.0, spot[1])), 1)}
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
    n = len(located)
    goal_ts = [g.t for g in in_play if g.type == "goal"]

    # When each beat's motion starts. A dribble's take-on starts DRIBBLE_LEAD s before the data
    # moment: he squares up to his man and goes round him, and is past him at the real moment
    # (when the call locks), instead of jumping 5 m in a third of a second.
    mt: list[float] = []
    for k, b in enumerate(located):
        m = b.t
        if b.data["type"] == "dribble":
            prev = mt[k - 1] if k else play.start_t
            m = min(b.t, max(prev + 0.3, b.t - DRIBBLE_LEAD))
        mt.append(m)

    def m_at(k: int) -> float:
        return mt[k] if k < n else play.end_t

    def waiting(k: int) -> tuple[str, list] | None:
        """(receiver, landing spot) of the pass made at beat ``k``, if it finds a teammate."""
        if k + 1 >= n or located[k].data["type"] != "pass" or not located[k].data["end_xy"]:
            return None
        b, nxt = located[k], located[k + 1]
        return (nxt.player_id, b.data["end_xy"]) if nxt.team == b.team and nxt.player_id else None

    def target(k: int) -> tuple[tuple[float, float], str | None]:
        """Ball spot and holder at beat ``k`` (after the last beat: where its ball ends up)."""
        if k < n:
            b = located[k]
            if (t := take_on(k)):  # he runs at his man from a few metres back
                return t["approach"], b.player_id
            return tuple(b.data["xy"]), b.player_id
        last = located[-1] if located else None
        if last is None:
            return _CENTRE, None
        end = last.data["end_xy"] or last.data["xy"]
        holder = last.player_id if last.data["type"] in ("carry", "dribble") else None
        return tuple(end), holder

    def attack_dir(team: str) -> float:
        return 1.0 if team == HOME else -1.0

    pressers: dict[int, tuple[str, tuple[float, float]]] = {}

    def take_on(k: int) -> dict | None:
        """The take-on of the dribble at beat ``k`` (the data spot is where he beats his man).

        He runs at the defender from ``approach``, steps to the ``side`` as the defender lunges,
        and bursts ``past`` him by the lock; then on to the next beat, always moving forward.
        A dot is ~4 m wide on a phone, so the move is drawn wide enough to read.
        """
        if k >= n or located[k].data["type"] != "dribble":
            return None
        if k not in pressers:
            b = located[k]
            xy = tuple(b.data["xy"])
            nxt = located[k + 1].data["xy"] if k + 1 < n else None
            dx, dy = ((nxt[0] - xy[0], nxt[1] - xy[1]) if nxt and _dist(nxt, xy) > 0.5
                      else (attack_dir(b.team), 0.0))
            norm = (dx * dx + dy * dy) ** 0.5
            fx, fy = dx / norm, dy / norm
            side = 1.0 if xy[1] < 34 else -1.0  # go round him on the side toward the middle
            lx, ly = -fy * side, fx * side
            if ly * side < 0:
                lx, ly = -lx, -ly
            shape = team_shape(on_pitch(slots, play.half, b.t), xy, b.player_id)
            them = [p for p in shape if p["team"] != TEAMS[b.team]["code"] and p["num"] != 1]
            near = min(them, key=lambda p: _dist((p["x"], p["y"]), xy))
            length = _dist(nxt, xy) if nxt else 6.0
            ahead = length * 0.9 if length >= 5.0 else 4.5

            def at(f: float, lat: float) -> tuple[float, float]:
                return (min(104.5, max(0.5, xy[0] + fx * f + lx * lat)),
                        min(67.5, max(0.5, xy[1] + fy * f + ly * lat)))
            pressers[k] = {
                "id": near["id"],
                "approach": at(-3.0, 0.0),  # runs at him
                "front": at(0.8, 0.0),  # the defender squares up on the spot
                "lunge": at(0.5, 1.3),  # ...and bites
                "side": at(-0.5, 5.5),  # the step round him
                "past": at(ahead, 1.5)}  # the burst past him
        return pressers[k]
        return pressers[k]

    def presser(k: int) -> tuple[str, tuple[float, float]] | None:
        """The defender a dribbler takes on at beat ``k``, where he meets him."""
        t = take_on(k)
        return (t["id"], t["front"]) if t else None

    def keeper(k: int) -> tuple[str, tuple[float, float]] | None:
        """After the shot at beat ``k``: the other side's keeper goes for it (and misses a goal)."""
        if k >= n or located[k].data["type"] != "shot" or not located[k].data["end_xy"]:
            return None
        b = located[k]
        end = b.data["end_xy"]
        goal = any(abs(t - b.t) < 2.0 for t in goal_ts)
        gx = 104.0 if attack_dir(b.team) > 0 else 1.0
        y = min(38.5, max(29.5, end[1]))
        if goal:  # dives the wrong way, or just too late
            y = y + (2.2 if end[1] < 34 else -2.2)
        gk = next((s for s in on_pitch(slots, play.half, b.t)
                   if s.team != b.team and s.position == "Goalkeeper"), None)
        return (gk.player_id, (gx, y)) if gk else None

    def placed(k: int) -> dict[str, tuple[float, float]]:
        """Players put somewhere other than their formation spot at beat ``k``."""
        out: dict[str, tuple[float, float]] = {}
        t_k = located[k].t if k < n else play.end_t
        ball_k, holder_k = target(k)
        spots = None
        for j in range(k, n):  # receivers of passes still to come run to where the ball lands
            w = waiting(j)
            if not w or w[0] == holder_k:
                continue
            b = located[j]
            flight = max(0.35, _dist(b.data["xy"], b.data["end_xy"]) / PASS_SPEED)
            left = b.t + flight - t_k  # until the ball gets there
            if left > 9.0:
                break
            if spots is None:
                spots = {p["id"]: (p["x"], p["y"])
                         for p in team_shape(on_pitch(slots, play.half, t_k), ball_k, holder_k)}
            home = spots.get(w[0])
            if home is None:
                continue
            need = _dist(home, w[1]) / RUN_IN
            if left < need:  # he is on his way: as far along as he needs to be to arrive in time
                f = 1.0 - left / need if need > 0 else 1.0
                out[w[0]] = (home[0] + (w[1][0] - home[0]) * f, home[1] + (w[1][1] - home[1]) * f)
        if k and located[k - 1].data["type"] == "pass" and located[k - 1].player_id:
            # the passer follows his pass a step or two instead of jogging back into shape
            p = located[k - 1]
            d = attack_dir(p.team)
            out[p.player_id] = (p.data["xy"][0] + 1.5 * d, p.data["xy"][1])
        for who in (presser(k), keeper(k - 1) if k else None):
            if who:  # facing the dribbler, or the keeper after a shot
                out[who[0]] = who[1]
        if k and (t := take_on(k - 1)):  # beaten: he stays where he lunged
            out[t["id"]] = t["lunge"]
        return out

    def shape_at(at_s: float, k: int, dur: float) -> Cue:
        ball, holder = target(k)
        t = located[k].t if k < n else play.end_t
        return _shape_cue(slots, at_s, play.half, t, ball, holder, dur, placed(k))

    # placed during the jump card, so the cut to the new clip never shows players teleporting
    arrives: dict[float, float] = {}  # pass time -> when the ball reaches the receiver
    cues.append(shape_at(max(0.0, at - JUMP_CARD_S), 0, 0.0))
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
        nxt = located[k + 1] if k + 1 < n else None
        dur = max(0.0, m_at(k + 1) - mt[k])
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
        if kind == "pass":
            arrives[round(b.t, 3)] = b.t + flight
        if (t := take_on(k)) and mt[k] < b.t:
            # the take-on: a step to the side (the defender bites), then a burst past him,
            # done by the real moment; the shape after it goes on to the next beat
            lead = b.t - mt[k]
            step = round(lead * 0.45, 3)
            cues.append(_shape_cue(slots, at + (mt[k] - play.start_t), play.half, b.t,
                                   t["side"], b.player_id, step,
                                   {**placed(k + 1), t["id"]: t["lunge"]}))
            cues.append(_shape_cue(slots, at + (mt[k] + step - play.start_t), play.half, b.t,
                                   t["past"], b.player_id, lead - step,
                                   {**placed(k + 1), t["id"]: t["lunge"]}))
            cues[-3] = Cue(when, "demo.event", {**cues[-3].data})
            dur = max(0.0, m_at(k + 1) - b.t)
            cues[-3] = Cue(when, "demo.event", {**cues[-3].data, "dur": round(dur, 3)})
            cues.append(shape_at(when, k + 1, dur))
            continue
        cues.append(shape_at(at + (mt[k] - play.start_t), k + 1, dur))
    for c in calls:  # open as the pass reaches him, lock on his action (select_calls: no overlap)
        player = display_name(c.player_id)
        # the card shows when the ball gets to him (he is on it), yet always >= 3 s before the lock
        shown = min(max(c.open_t, arrives.get(round(c.open_t, 3), c.open_t)), c.lock_t - MIN_OPEN_S)
        cues.append(Cue(at + (max(c.open_t, shown) - play.start_t), "call.open", {
            "call_id": c.call_id, "player": player, "team": TEAMS[c.team]["code"],
            "prompt": lines[c.call_id].setup, "options": list(OPTIONS)}))
        cues.append(Cue(at + (c.lock_t - play.start_t), "call.lock", {"call_id": c.call_id}))
    return cues


def build_timeline(match: Match, script: tuple | None = None, client: LLMClient | None = None,
                   team: str = "NBR") -> Timeline:
    """Walk ``script`` with a real-time cursor and emit every cue, sorted by (at, rank).

    ``team`` (NBR / CAL) is the fan's side: its highlights cut (``SCRIPTS``) unless ``script`` is
    given, and calls on that side's players only. Call lines come from ``client`` (default: the
    committed LLM cache, never the network).
    """
    script = script if script is not None else SCRIPTS[team]
    side = HOME if team == "NBR" else AWAY
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
