"""sim_placeholder.py — seeded generator of the PLACEHOLDER simulated match dataset.

Last Modified: 2026-10-24 22:30, first version: scripted 2-1 match with tracking and labels
Inputs:  none (seed 42)
Outputs: data/source/sim-placeholder/{roster.json, events.jsonl, tracking.csv, labels.json,
         README.md}

Workflow:
  - A script of phases (calm circulation, build-up, fast attack, set pieces, stoppages) is run
    by a small state machine with a seeded RNG; each phase emits events, tracking segments,
    ball waypoints and ground-truth labels.
  - Tracking is built afterwards: formation slots shifted with the ball, noise scaled by the
    phase intensity, one frame per match second in the FIXED frame (home attacks left to right).
  - Events use the per-acting-team frame (the acting team attacks left to right).

CAUTION: placeholder data. Replace with the teammate's dataset; the same tests run against it.
"""

from __future__ import annotations

import argparse
import bisect
import json
import math
import random
from pathlib import Path

from backend.core.constants import (DEFAULT_MATCH_ID, PITCH_LENGTH_M, PITCH_WIDTH_M,
                                    SECOND_HALF_START_S)

# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
#
# Constants
#
# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

SEED = 42
HOME, AWAY = "NBR", "CAL"
HOME_NAME, AWAY_NAME = "Northbridge FC", "Calder Town"
DEFAULT_OUT_DIR = Path(__file__).resolve().parents[2] / "data" / "source" / "sim-placeholder"

# (number, surname, position, starter); starters are listed in formation slot order (4-4-2).
SQUADS: dict[str, list[tuple[int, str, str, bool]]] = {
    HOME: [(1, "Pryce", "GK", True), (2, "Brandt", "DF", True), (3, "Hale", "DF", True),
           (4, "Vance", "DF", True), (5, "Ito", "DF", True), (6, "Lindqvist", "MF", True),
           (7, "Rossi", "MF", True), (8, "Okafor", "MF", True), (10, "Novak", "MF", True),
           (9, "Mensah", "FW", True), (11, "Castillo", "FW", True),
           (14, "Bell", "DF", False), (15, "Marlow", "MF", False), (17, "Sato", "FW", False)],
    AWAY: [(1, "Ward", "GK", True), (2, "Kerr", "DF", True), (3, "Abara", "DF", True),
           (4, "Teague", "DF", True), (5, "Moreau", "DF", True), (6, "Duarte", "MF", True),
           (7, "Pike", "MF", True), (8, "Haldane", "MF", True), (10, "Osei", "MF", True),
           (9, "Doyle", "FW", True), (11, "Varga", "FW", True),
           (12, "Lowe", "DF", False), (14, "Brook", "MF", False), (16, "Nkemelu", "FW", False)],
}
# Substitutions in order: (player off number, player on number).
SUB_PLAN = {HOME: [(11, 17), (7, 15), (3, 14)], AWAY: [(11, 16), (10, 14), (2, 12)]}

# Formation slot base positions in the team's attacking frame, by slot index.
SLOT_BASE = [(5.0, 34.0), (25.0, 12.0), (25.0, 26.0), (25.0, 42.0), (25.0, 56.0),
             (50.0, 10.0), (50.0, 26.0), (50.0, 42.0), (50.0, 58.0), (72.0, 26.0), (72.0, 42.0)]
SLOT_BALL_PULL = [0.1, 0.5, 0.5, 0.5, 0.5, 0.6, 0.6, 0.6, 0.6, 0.6, 0.6]  # x shift per ball metre
LATERAL_PULL = 0.25

# Tracking intensity by phase kind: (noise sigma m, pull to target per s, speed cap m/s).
TRACK_KINDS = {
    "calm": (0.55, 0.12, 1.6), "build": (1.4, 0.25, 3.5), "attack": (3.0, 0.35, 8.0),
    "stalled": (0.3, 0.1, 1.4), "stoppage": (0.2, 0.1, 1.0), "setpiece": (0.25, 0.1, 1.0),
}

ZONES = {"back": (2.0, 34.0), "mid": (36.0, 68.0)}  # x range of circulation, acting frame
W_BACK = {"GK": 0.08, "DF": 0.5, "MF": 0.37, "FW": 0.05}
W_MID = {"GK": 0.02, "DF": 0.25, "MF": 0.55, "FW": 0.18}
W_ATTACK = {"MF": 0.42, "FW": 0.45, "DF": 0.13}
W_SHOT = {"FW": 0.7, "MF": 0.27, "DF": 0.03}
W_RESTART = {"DF": 0.6, "MF": 0.4}

CALM_DT = (3.0, 5.0)  # seconds between passes in circulation
BUILD_DT = (2.0, 3.0)
ATTACK_DT = (1.0, 2.0)
CALM_CARRY_P = 0.12
CALM_INCOMPLETE_P = 0.04  # mid-third only; flips possession
CROSS_CLEAR_P = 0.6  # attack with a cleared cross before the final shot
MIN_CALM_S = 20.0
CORNER_WAIT_S, FREE_KICK_WAIT_S, PENALTY_WAIT_S = 20.0, 25.0, 60.0
THROW_WAIT_S, GOAL_KICK_WAIT_S, FOUL_FK_WAIT_S = 7.5, 11.5, 12.0
WAIT_JITTER_S = 2.0
THROW_JITTER_S, GOAL_KICK_JITTER_S = 2.5, 3.5  # throw-in waits 5-10 s, goal-kick waits 8-15 s
SHORT_PLAY_DT = (2.5, 3.5)  # seconds between passes of the short play after a restart
SHORT_PLAY_PASSES = 2
CARD_P = 0.2  # share of the midfield fouls that earn a yellow card
STALLED_S = (14.0, 25.0)  # length of a stalled spell
STALLED_X = (70.0, 85.0)
MIN_FLIGHT_S, MAX_FLIGHT_S = 0.5, 2.0  # ball flight time bounds for a pass
BALL_FLIGHT_SPEED = 25.0  # m/s, slowest flight allowed for a long pass
BALL_PLACE_SPEED = 20.0  # m/s, ball carried to the spot of a restart
REACH_M = 18.0  # farthest an actor may be from the ball in his formation spot
GK_MAX_X, GK_HOLD_X = 25.0, 10.0  # the keeper acts within 25 m of his goal; holds up to 10 m
BYLINE_APPROACH_X = 75.0
LONG_PASS_M = 30.0
BUILD_END_X = 58.0
ZONE_SLACK_M, ZONE_STEP_M = 3.0, 15.0
FOUL_X = (20.0, 70.0)  # midfield fouls happen with the ball in this x range
SAVE_X = 103.5  # a saved shot is met this far up the pitch
LEAD_S = 10  # the acting player starts running to the ball this many seconds early
ACTOR_RUN_MS = 8.0
KICKOFF_LEAD_S = 2  # a player acting this soon after the half starts begins on the ball
RUN_MS = 5.0  # speed assumed when choosing who can reach the next action
MAX_SPEED_MS = 9.5
POSSESSION_TYPES = frozenset({"pass", "carry", "shot", "goal", "kickoff", "throw_in", "goal_kick",
                              "corner", "free_kick", "penalty", "interception", "tackle",
                              "clearance", "save"})
SUB_STOP_S, INJURY_STOP_S, WATER_BREAK_S = 25.0, 60.0, 90.0
CELEBRATION_S, VAR_S = 30.0, 45.0
FIRST_HALF_END_T, FULL_END_T = 2790.0, 5580.0
SHOT_BOX_X = (88.0, 96.0)  # shot spot x range
FOUL_FK_X = (84.0, 88.0)
PENALTY_SPOT = (94.0, 34.0)
CENTRE = (PITCH_LENGTH_M / 2, PITCH_WIDTH_M / 2)

# The match script. Entries are (phase, *args); calm args are (team, zone, until).
SCRIPT: list[tuple] = [
    ("kickoff", HOME), ("calm", HOME, "mid", 95.0),
    ("build", HOME), ("attack", HOME, "corner"), ("corner", HOME),
    ("calm", AWAY, "back", 270.0), ("throw", AWAY), ("throws", 4),
    ("build", AWAY), ("attack", AWAY, "off"), ("goalkick", HOME), ("goalkicks", 2),
    ("calm", HOME, "mid", 520.0),
    ("build", HOME), ("attack", HOME, "foul"), ("freekick", HOME), ("goalkick", AWAY),
    ("fouls", 2),
    ("calm", AWAY, "back", 700.0),
    ("build", AWAY), ("attack", AWAY, "out"), ("throw", HOME), ("throws", 4),
    ("calm", HOME, "mid", 880.0),
    ("build", HOME), ("attack", HOME, "goal_var"),
    ("calm", AWAY, "mid", 1100.0), ("foulplay", HOME),
    ("calm", HOME, "mid", 1315.0), ("water", 1320.0),
    ("calm", AWAY, "back", 1520.0),
    ("build", AWAY), ("attack", AWAY, "corner"), ("corner", AWAY),
    ("build", HOME), ("attack", HOME, "deflect"), ("corner", HOME),
    ("calm", HOME, "mid", 1715.0),
    ("build", HOME), ("attack", HOME, "save"), ("goalkicks", 2),
    ("calm", AWAY, "back", 1880.0), ("stalled", AWAY),
    ("build", AWAY), ("attack", AWAY, "goal"),
    ("build", HOME), ("attack", HOME, "deflect"), ("corner", HOME), ("throws", 4),
    ("calm", HOME, "mid", 2195.0), ("stalled", HOME), ("injury", AWAY), ("fouls", 1),
    ("calm", AWAY, "mid", 2460.0),
    ("build", AWAY), ("attack", AWAY, "foul"), ("freekick", AWAY), ("goalkick", HOME),
    ("throws", 2), ("goalkicks", 1),
    ("calm", HOME, "back", 2740.0),
    ("counter", AWAY, "save"),
    ("calm", HOME, "mid", 2788.0), ("half_end", FIRST_HALF_END_T),
    # --- second half
    ("half2",), ("kickoff", AWAY), ("calm", AWAY, "mid", 2800.0),
    ("build", AWAY), ("attack", AWAY, "out"), ("throw", HOME), ("throws", 4), ("goalkicks", 1),
    ("calm", HOME, "back", 3065.0),
    ("build", HOME), ("attack", HOME, "corner"), ("corner", HOME), ("sub", HOME),
    ("build", AWAY), ("attack", AWAY, "deflect"), ("corner", AWAY), ("fouls", 3),
    ("calm", AWAY, "mid", 3315.0),
    ("build", AWAY), ("attack", AWAY, "save"), ("sub", AWAY), ("goalkicks", 2),
    ("calm", HOME, "mid", 3500.0),
    ("build", HOME), ("attack", HOME, "foul_box"), ("penalty", HOME), ("fouls", 3),
    ("calm", AWAY, "mid", 3800.0), ("sub", HOME), ("sub", AWAY), ("throws", 4),
    ("calm", HOME, "back", 4015.0), ("water", 4020.0),
    ("calm", AWAY, "mid", 4350.0),
    ("build", AWAY), ("attack", AWAY, "foul"), ("freekick", AWAY), ("goalkick", HOME),
    ("throws", 4),
    ("calm", HOME, "mid", 4550.0),
    ("build", HOME), ("attack", HOME, "save"), ("sub", HOME), ("sub", AWAY), ("fouls", 3),
    ("calm", AWAY, "back", 4850.0), ("stalled", AWAY),
    ("build", AWAY), ("attack", AWAY, "corner"), ("corner", AWAY),
    ("build", HOME), ("attack", HOME, "deflect"), ("corner", HOME), ("stalled", AWAY),
    ("throws", 4),
    ("calm", HOME, "mid", 5150.0), ("foulplay", AWAY), ("throws", 4), ("fouls", 3),
    ("build", HOME), ("attack", HOME, "save"),
    ("calm", AWAY, "mid", 5450.0),
    ("build", HOME), ("attack", HOME, "off"), ("goalkick", AWAY),
    ("calm", HOME, "mid", 5575.0), ("full_end", FULL_END_T),
]


# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
#
# Helpers
#
# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%


def player_id(team: str, number: int, surname: str) -> str:
    """Stable player id, e.g. ``nbr-8-okafor``."""
    return f"{team.lower()}-{number}-{surname.lower()}"


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _pt(x: float, y: float) -> tuple[float, float]:
    """Point on the pitch, rounded to 0.1 m."""
    return (round(_clamp(x, 0.0, PITCH_LENGTH_M), 1), round(_clamp(y, 0.0, PITCH_WIDTH_M), 1))


def _flip(xy: tuple[float, float]) -> tuple[float, float]:
    """Mirror a point between the two teams' attacking frames."""
    return (PITCH_LENGTH_M - xy[0], PITCH_WIDTH_M - xy[1])


def _roster_json() -> dict:
    players = [{"player_id": player_id(team, n, s), "name": s, "team": team, "position": pos,
                "number": n, "starter": st}
               for team, squad in SQUADS.items() for n, s, pos, st in squad]
    return {"match_id": DEFAULT_MATCH_ID, "home": HOME, "away": AWAY, "home_name": HOME_NAME,
            "away_name": AWAY_NAME, "players": players}


# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
#
# Match simulation (events, segments, labels)
#
# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%


class _Sim:
    """Runs the script and collects events, tracking segments, ball waypoints and labels.

    Every phase starts where the previous one left the ball (``self.ball``, fixed frame) and
    every change of possession is explained by an event (``_take``).
    """

    def __init__(self, seed: int) -> None:
        self.rng = random.Random(seed)
        self.t = 0.0
        self.half = 1
        self.events: list[tuple[int, float, int, dict]] = []  # (half, t, seq, event)
        self.segments: list[tuple[int, float, float, str, str]] = []  # half,t0,t1,kind,team
        self.waypoints: list[tuple[int, float, tuple[float, float]]] = []  # fixed frame
        self.labels: list[dict] = []
        self.pos_of: dict[str, str] = {}
        self.on_pitch: dict[str, list[str]] = {HOME: [], AWAY: []}
        self.bench: dict[str, dict[int, str]] = {HOME: {}, AWAY: {}}
        self.initial_slots: dict[str, list[str]] = {HOME: [], AWAY: []}
        self.sub_log: list[tuple[int, float, str, int, str]] = []  # half,t,team,slot,new pid
        self.sub_count = {HOME: 0, AWAY: 0}
        self.last_foul: tuple[str, str, tuple[float, float]] | None = None
        self.last_actor: str | None = None
        self.recent: dict[str, tuple[float, tuple[float, float]]] = {}  # pid -> (t, fixed spot)
        self.ball: tuple[float, float] = CENTRE  # fixed frame
        self.poss: str | None = None
        self.flight = 0.0
        for team, squad in SQUADS.items():
            for n, s, pos, starter in squad:
                pid = player_id(team, n, s)
                self.pos_of[pid] = pos
                if starter:
                    self.on_pitch[team].append(pid)
                else:
                    self.bench[team][n] = pid
            self.initial_slots[team] = list(self.on_pitch[team])
        self.number_of = {player_id(tm, n, s): n for tm, sq in SQUADS.items() for n, s, _, _ in sq}

    # .........................................................................
    # primitives
    # .........................................................................

    def opp(self, team: str) -> str:
        return AWAY if team == HOME else HOME

    def _fixed(self, team: str, xy: tuple[float, float]) -> tuple[float, float]:
        return xy if team == HOME else _flip(xy)

    def bpos(self, team: str) -> tuple[float, float]:
        """Ball position in `team`'s attacking frame."""
        return self._fixed(team, self.ball)

    def emit(self, typ: str, team: str, player: str | None = None,
             start: tuple[float, float] | None = None, end: tuple[float, float] | None = None,
             outcome: str | None = None, related: str | None = None, dt: float = 3.0,
             at: float | None = None, track_ball: bool = True) -> float:
        """Append one event (acting-team frame) and its ball waypoints; returns its time.

        `dt` is the time until the next event, which caps the ball's flight time.
        `track_ball=False` records coordinates without moving the ball (a goal repeats the
        shot's coordinates; the shot already carried the ball).
        """
        t = round(self.t if at is None else at, 1)
        ev: dict = {"t": t, "half": self.half, "team": team, "type": typ, "player_id": player}
        if related is not None:
            ev["related_player_id"] = related
        self.flight = 0.0
        if start is not None:
            ev["start"] = list(start)
            if track_ball:
                self.waypoints.append((self.half, t, self._fixed(team, start)))
                self.ball = self._fixed(team, start)
        if end is not None:
            ev["end"] = list(end)
            if track_ball:
                self.flight = max(MIN_FLIGHT_S, min(dt - 1.0, MAX_FLIGHT_S),
                                  math.dist(start, end) / BALL_FLIGHT_SPEED)
                self.waypoints.append((self.half, t + self.flight, self._fixed(team, end)))
                self.ball = self._fixed(team, end)
        if outcome is not None:
            ev["outcome"] = outcome
        if player is not None and start is not None:
            self.recent[player] = (t, self._fixed(team, start))
        if typ in POSSESSION_TYPES:
            self.poss = team
        elif typ == "foul":
            self.poss = self.opp(team)
        elif typ == "out":
            self.poss = None
        self.events.append((self.half, t, len(self.events), ev))
        return t

    def seg(self, kind: str, t0: float, t1: float, team: str) -> None:
        self.segments.append((self.half, t0, t1, kind, team))

    def label(self, kind: str, t0: float, t1: float, note: str) -> None:
        self.labels.append({"kind": kind, "half": self.half, "t_start": round(t0, 1),
                            "t_end": round(t1, 1), "note": note})

    def _slot_dist(self, team: str, pid: str, at: tuple[float, float]) -> float:
        """Distance from the player's formation spot (shifted to a ball at `at`) to `at`."""
        slot = self.initial_slots[team].index(pid) if pid in self.initial_slots[team] else None
        if slot is None:  # a substitute takes over the slot of the player he replaced
            slot = next(s for h, t, tm, s, new in self.sub_log if new == pid)
        bx, by = SLOT_BASE[slot]
        tx = bx + SLOT_BALL_PULL[slot] * (at[0] - CENTRE[0])
        ty = by + LATERAL_PULL * (at[1] - CENTRE[1])
        return math.dist((tx, ty), at)

    def pick(self, team: str, weights: dict[str, float], at: tuple[float, float] | None = None
             ) -> str:
        """Random on-pitch player of `team` for an action at `at`, position drawn from `weights`.

        Positions whose players would have to run more than REACH_M are dropped, and the
        goalkeeper only acts within GK_MAX_X of his own goal. With no candidate left the
        nearest outfield player is used.
        """
        pools: dict[str, list[str]] = {}
        if at is not None:
            spot = self._fixed(team, at)

            def can_reach(p: str) -> bool:  # he must be able to run from his last action
                last = self.recent.get(p)
                return last is None or math.dist(last[1], spot) <= RUN_MS * max(
                    self.t - last[0], 0.5)
        else:
            def can_reach(p: str) -> bool:
                return True
        for pos in weights:
            pool = [p for p in self.on_pitch[team]
                    if self.pos_of[p] == pos and p != self.last_actor]
            if at is not None:
                pool = [p for p in pool
                        if self._slot_dist(team, p, at) <= REACH_M and can_reach(p)]
                if pos == "GK" and at[0] > GK_MAX_X:
                    pool = []
            if pool:
                pools[pos] = pool
        if not pools:
            outfield = [p for p in self.on_pitch[team] if self.pos_of[p] != "GK" and can_reach(p)]
            outfield = outfield or [p for p in self.on_pitch[team] if self.pos_of[p] != "GK"]
            self.last_actor = min(outfield, key=lambda p: self._slot_dist(team, p, at or CENTRE))
            return self.last_actor
        pos = self.rng.choices(list(pools), weights=[weights[p] for p in pools])[0]
        self.last_actor = self.rng.choice(pools[pos])
        return self.last_actor

    def of_pos(self, team: str, pos: str) -> str:
        return self.rng.choice([p for p in self.on_pitch[team] if self.pos_of[p] == pos])

    def wait(self, base: float, jitter: float = WAIT_JITTER_S) -> float:
        return base + self.rng.uniform(-jitter, jitter)

    def _place_time(self, dest: tuple[float, float]) -> float:
        """Time at which a restart at `dest` (acting frame of the awarded team) is awarded."""
        team = self.poss or HOME
        gap = math.dist(self.bpos(team), dest) / BALL_PLACE_SPEED
        return self.t + max(1.0, gap)

    def _take(self, team: str) -> None:
        """Give `team` the ball; an interception explains a change of possession."""
        if self.poss is not None and self.poss != team:
            x, y = self.bpos(team)
            self.emit("interception", team, self.pick(team, W_MID, (x, y)), _pt(x, y))
            self.t += 1.0
        self.poss = team

    def _pass_to(self, team: str, dest: tuple[float, float], weights: dict[str, float],
                 dt: float, outcome: str = "complete") -> float:
        """Pass from the ball to `dest`; advances the clock by `dt`; returns the pass time."""
        x, y = self.bpos(team)
        t = self.emit("pass", team, self.pick(team, weights, (x, y)), _pt(x, y), _pt(*dest),
                      outcome, dt=dt)
        self.t += dt
        return t

    def _ensure_out(self, loser: str, kind: str) -> None:
        """Make `loser` put the ball out over a touchline (`touch`) or its attacker's byline."""
        last = self.events[-1][3] if self.events else {}
        if last.get("type") == "out" and last["team"] == loser:
            sx, sy = last["start"]
            if (kind == "touch" and sy in (0.0, PITCH_WIDTH_M)) or (kind == "byline" and sx >= 100):
                return
        self._take(loser)
        x, y = self.bpos(loser)
        if kind == "touch":
            edge = 0.0 if y < PITCH_WIDTH_M / 2 else PITCH_WIDTH_M
            dest = (_clamp(x + self.rng.gauss(0, 5), 20, 90), edge)
            t = self._pass_to(loser, dest, W_MID, 3.0, "incomplete")
            t_out = self.emit("out", loser, None, _pt(*dest), at=t + self.flight)
        else:
            while x < BYLINE_APPROACH_X:
                far = min(x + LONG_PASS_M, self.rng.uniform(80, 90))
                self._pass_to(loser, (far, _clamp(y + self.rng.gauss(0, 8), 6, 62)), W_MID, 2.5)
                x, y = self.bpos(loser)
            side = self.rng.choice([-1, 1])
            end = (105.0, 34 + side * self.rng.uniform(6, 14))
            t = self.emit("shot", loser, self.pick(loser, W_SHOT, (x, y)), _pt(x, y), _pt(*end),
                          "off_target", dt=2.0)
            t_out = self.emit("out", loser, None, _pt(*end), at=t + self.flight)
        self.seg("build", t, t_out, loser)
        self.t = t_out

    # .........................................................................
    # open-play phases
    # .........................................................................

    def kickoff(self, team: str) -> None:
        self.emit("kickoff", team, start=CENTRE)
        self.t += 1.5

    def calm(self, team: str, zone: str, until: float) -> None:
        """Slow circulation until `until`; the whole spell is a labelled calm spell."""
        lo, hi = ZONES[zone]
        weights = W_BACK if zone == "back" else W_MID
        self._take(team)
        self.last_actor = None
        x, y = self.bpos(team)
        while not lo - ZONE_SLACK_M <= x <= hi + ZONE_SLACK_M:  # carry the ball into the zone
            dest = (_clamp(x, lo + 2, hi - 2), y)
            step = (dest[0] - x) * min(1.0, ZONE_STEP_M / abs(dest[0] - x))
            self._pass_to(team, (x + step, y), weights, 2.5)
            x, y = self.bpos(team)
        if until - self.t < MIN_CALM_S:
            raise ValueError(f"calm spell at t={self.t:.0f} would be under {MIN_CALM_S:.0f} s")
        t0, team0 = self.t, team
        while True:
            dt = self.rng.uniform(*CALM_DT)
            if self.t + dt > until:
                break
            nx = _clamp(x + self.rng.gauss(0, 8), lo, hi)
            ny = _clamp(y + self.rng.gauss(0, 13), 6, 62)
            who = self.of_pos(team, "GK") if x <= GK_HOLD_X else self.pick(team, weights, (x, y))
            if self.rng.random() < CALM_CARRY_P:
                self.emit("carry", team, who, _pt(x, y), _pt(nx, ny), "complete", dt=dt)
            elif zone == "mid" and self.rng.random() < CALM_INCOMPLETE_P:
                self.emit("pass", team, who, _pt(x, y), _pt(nx, ny), "incomplete", dt=dt)
                other = self.opp(team)
                mx, my = _flip((nx, ny))
                self.t += dt
                self.emit("interception", other, self.pick(other, W_MID, (mx, my)),
                          _pt(mx, my))
                team, x, y = other, _clamp(mx, lo, hi), my
                continue
            else:
                self.emit("pass", team, who, _pt(x, y), _pt(nx, ny), "complete", dt=dt)
            x, y = nx, ny
            self.t += dt
        self.t = max(self.t, until)
        self.seg("calm", t0, until, team0)
        note = f"{team0} circulation in the {zone} third"
        if team != team0:
            note += f", {team} wins the ball and keeps it"
        self.label("calm", t0, until, note)

    def build(self, team: str) -> None:
        """Unlabelled build-up from where the ball is to the edge of the final third."""
        self._take(team)
        t0 = self.t
        x, y = self.bpos(team)
        n = self.rng.randint(4, 5)
        for i in range(n):
            dt = self.rng.uniform(*BUILD_DT)
            nx = x + (BUILD_END_X - x) / (n - i) + self.rng.gauss(0, 1.5)
            ny = _clamp(y + self.rng.gauss(0, 8), 6, 62)
            self.emit("pass", team, self.pick(team, W_MID, (x, y)), _pt(x, y), _pt(nx, ny),
                      "complete", dt=dt)
            x, y = nx, ny
            self.t += dt
        self.seg("build", t0, self.t, team)

    def attack(self, team: str, ending: str, counter: bool = False) -> None:
        """Fast attack ending in a shot, foul or ball out. Labelled from first to last event."""
        rng, other = self.rng, self.opp(team)
        if counter:
            self.t += 3.0  # starts just after the calm spell ends
        t0 = self.t
        self._take(team)
        x, y = self.bpos(team)
        self.last_actor = None
        if ending == "foul":
            sx = rng.uniform(*FOUL_FK_X)
        elif ending == "foul_box":
            sx = 96.5
        else:
            sx = rng.uniform(*SHOT_BOX_X)
        sy = rng.uniform(20, 48) if ending == "foul" else rng.uniform(26, 42)
        n = rng.randint(4, 6)
        cross_at = n // 2 if ending not in ("goal", "goal_var") and rng.random() < CROSS_CLEAR_P \
            else None
        for i in range(n):
            dt = rng.uniform(*ATTACK_DT)
            if i == cross_at:
                box = (97.0, 34 + rng.uniform(-6, 6))
                self._pass_to(team, box, {"MF": 0.6, "FW": 0.4}, dt, "incomplete")
                cx, cy = _flip(_pt(*box))
                self.emit("clearance", other, self.pick(other, {"DF": 1.0}, (cx, cy)),
                          _pt(cx, cy), _pt(30, rng.uniform(15, 53)), dt=dt)
                self.t += dt
                rx, ry = self.bpos(team)
                self.emit("interception", team, self.pick(team, {"MF": 1.0}, (rx, ry)),
                          _pt(rx, ry))
                self.t += dt
                x, y = self.bpos(team)
                continue
            if i == n - 1:
                nx, ny = sx, sy
            else:
                nx = x + (sx - x) / (n - i) + rng.gauss(0, 2)
                ny = _clamp(y + (sy - y) / (n - i) + rng.gauss(0, 5), 5, 63)
            kind = "carry" if rng.random() < 0.35 else "pass"
            self.emit(kind, team, self.pick(team, W_ATTACK, (x, y)), _pt(x, y), _pt(nx, ny),
                      "complete", dt=dt)
            x, y = nx, ny
            self.t += dt
        t_end = self._attack_end(team, other, ending, sx, sy)
        self.seg("attack", t0, t_end + 1.0, team)
        self.label("attack", t0, t_end, f"{team} fast attack ending in {ending}")
        self.t = t_end + 1.0
        if ending in ("goal", "goal_var"):
            self._after_goal(team, var=ending == "goal_var")

    def _attack_end(self, team: str, other: str, ending: str, sx: float, sy: float) -> float:
        """Emit the closing events of an attack; returns the time of the last one."""
        rng = self.rng
        sx, sy = self.bpos(team)
        shooter = self.pick(team, W_SHOT, (sx, sy))
        if ending in ("save", "corner"):
            ty = 34 + rng.gauss(0, 2.5)
            t = self.emit("shot", team, shooter, _pt(sx, sy), _pt(SAVE_X, ty), "saved", dt=1.0)
            gk = self.of_pos(other, "GK")
            sv = _flip(_pt(SAVE_X, ty))
            t += self.flight
            if ending == "save":
                return self.emit("save", other, gk, _pt(*sv), outcome="saved", at=t)
            parry = (0.0, _clamp(sv[1] + rng.choice([-8, 8]), 1, 67))
            t = self.emit("save", other, gk, _pt(*sv), _pt(*parry), "saved", dt=1.0, at=t)
            return self.emit("out", other, None, _pt(*parry), at=t + self.flight)
        if ending == "deflect":
            side = rng.choice([-1, 1])
            end = (105.0, 34 + side * rng.uniform(6, 10))
            t = self.emit("shot", team, shooter, _pt(sx, sy), _pt(*end), "off_target", dt=1.0)
            return self.emit("out", other, None, _pt(*_flip(_pt(*end))), at=t + self.flight)
        if ending == "off":
            side = rng.choice([-1, 1])
            end = (105.0, 34 + side * rng.uniform(6, 14))
            t = self.emit("shot", team, shooter, _pt(sx, sy), _pt(*end), "off_target", dt=1.0)
            return self.emit("out", team, None, _pt(*end), at=t + self.flight)
        if ending in ("foul", "foul_box"):
            fx, fy = _flip((sx, sy))
            fouler = self.pick(other, {"DF": 1.0}, (fx, fy))
            t = self.emit("foul", other, fouler, _pt(fx, fy), at=self.t + 0.5)
            self.last_foul = (fouler, other, (sx, sy))
            return t
        if ending == "out":
            edge = 0.0 if sy < PITCH_WIDTH_M / 2 else PITCH_WIDTH_M
            dest = (min(sx + 2, PITCH_LENGTH_M), edge)
            t = self.emit("pass", team, shooter, _pt(sx, sy), _pt(*dest), "incomplete", dt=2.0)
            return self.emit("out", team, None, _pt(*dest), at=t + self.flight)
        end = (105.0, 34 + rng.uniform(-2.5, 2.5))
        t = self.emit("shot", team, shooter, _pt(sx, sy), _pt(*end), "goal", dt=1.0)
        return self.emit("goal", team, shooter, _pt(sx, sy), _pt(*end), "goal",
                         at=t + self.flight, track_ball=False)

    def _after_goal(self, scorer: str, var: bool) -> None:
        """Celebration (or VAR check), then kickoff by the conceding team."""
        t_goal = self.t - 1.0
        conceding = self.opp(scorer)
        if var:
            t_var = self.emit("var_check", scorer, at=t_goal + 5.0)
            end = t_var + VAR_S
            self.seg("stoppage", t_goal, end, scorer)
            self.label("stoppage", t_var, end, "VAR check, goal stands")
        else:
            end = t_goal + CELEBRATION_S
            self.seg("stoppage", t_goal, end, scorer)
            self.label("stoppage", t_goal + 1.0, end, "goal celebration and restart")
        self.t = end
        self.kickoff(conceding)

    def stalled(self, team: str) -> None:
        """Slow passing around the edge of the box, no shot; labelled `stalled`."""
        self._take(team)
        self.last_actor = None
        x, y = self.bpos(team)
        while x < STALLED_X[0]:  # move the ball up to the box first (unlabelled)
            self._pass_to(team, (min(x + 15, STALLED_X[0] + 2), y), W_MID, 2.5)
            x, y = self.bpos(team)
        t0 = self.t
        until = t0 + self.rng.uniform(*STALLED_S)
        while self.t + 3.5 < until:
            dt = self.rng.uniform(2.5, 4.0)
            nx = _clamp(x + self.rng.gauss(0, 4), *STALLED_X)
            ny = _clamp(y + self.rng.gauss(0, 9), 8, 60)
            self.emit("pass", team, self.pick(team, W_ATTACK, (x, y)), _pt(x, y), _pt(nx, ny),
                      "complete", dt=dt)
            x, y = nx, ny
            self.t += dt
        self.label("stalled", t0, self.t, f"{team} passing around the box, no shot")
        other = self.opp(team)
        fx, fy = _flip((x, y))
        self.emit("tackle", other, self.pick(other, {"DF": 1.0}, (fx, fy)), _pt(fx, fy),
                  outcome="won")
        self.t += 1.0
        self.seg("stalled", t0, self.t, team)

    def shortplay(self, team: str) -> None:
        """A few passes drifting towards midfield after a restart (unlabelled)."""
        self._take(team)
        t0 = self.t
        x, y = self.bpos(team)
        for _ in range(SHORT_PLAY_PASSES):
            dt = self.rng.uniform(*SHORT_PLAY_DT)
            nx = x + (CENTRE[0] - x) * 0.6 + self.rng.gauss(0, 5)
            ny = _clamp(y + self.rng.gauss(0, 10), 6, 62)
            self.emit("pass", team, self.pick(team, W_MID, (x, y)), _pt(x, y), _pt(nx, ny),
                      "complete", dt=dt)
            x, y = nx, ny
            self.t += dt
        self.seg("calm", t0, self.t, team)

    # .........................................................................
    # restarts, referee decisions and stoppages
    # .........................................................................

    def corner(self, team: str) -> None:
        bx, by = self.bpos(team)
        spot = (105.0, 0.0 if by < PITCH_WIDTH_M / 2 else PITCH_WIDTH_M)
        t_award = self.emit("corner", team, start=spot, at=self._place_time(spot))
        t_kick = t_award + self.wait(CORNER_WAIT_S)
        self.seg("setpiece", t_award, t_kick, team)
        self.label("set_piece_wait", t_award, t_kick, f"{team} corner")
        self.t = t_kick
        box = (97.0, 34 + self.rng.uniform(-8, 8))
        self.emit("pass", team, self.pick(team, W_RESTART, spot), spot, _pt(*box), "complete",
                  dt=1.5)
        other = self.opp(team)
        cx, cy = _flip(_pt(*box))
        self.emit("clearance", other, self.pick(other, {"DF": 1.0}, (cx, cy)), _pt(cx, cy),
                  _pt(28, self.rng.uniform(15, 53)), dt=1.5, at=t_kick + 1.5)
        self.t = t_kick + 3.0

    def freekick(self, team: str) -> None:
        """Near-box free kick after the foul that ended the previous attack."""
        assert self.last_foul is not None
        _, _, (sx, sy) = self.last_foul
        t_award = self.emit("free_kick", team, start=_pt(sx, sy), at=self.t + 2.0)
        t_kick = t_award + self.wait(FREE_KICK_WAIT_S)
        self.seg("setpiece", t_award, t_kick, team)
        self.label("set_piece_wait", t_award, t_kick, f"{team} free kick near the box")
        self.t = t_kick
        side = self.rng.choice([-1, 1])
        end = (105.0, 34 + side * self.rng.uniform(8, 12))
        t = self.emit("shot", team, self.pick(team, W_SHOT, (sx, sy)), _pt(sx, sy), _pt(*end),
                      "off_target", dt=1.5)
        self.emit("out", team, None, _pt(*end), at=t + self.flight)
        self.t = t + self.flight + 1.0

    def penalty(self, team: str) -> None:
        assert self.last_foul is not None
        fouler, fteam, _ = self.last_foul
        self.emit("card", fteam, fouler, outcome="yellow", at=self.t + 1.0)
        t_award = self.emit("penalty", team, start=PENALTY_SPOT, at=self.t + 2.0)
        t_kick = t_award + self.wait(PENALTY_WAIT_S)
        self.seg("setpiece", t_award, t_kick, team)
        self.label("set_piece_wait", t_award, t_kick, f"{team} penalty")
        self.t = t_kick
        taker = self.of_pos(team, "FW")
        end = (105.0, 34 + self.rng.uniform(-2.5, 2.5))
        t = self.emit("shot", team, taker, PENALTY_SPOT, _pt(*end), "goal", dt=1.0)
        self.emit("goal", team, taker, PENALTY_SPOT, _pt(*end), "goal", at=t + self.flight,
                  track_ball=False)
        self.t = t + self.flight + 1.0
        self._after_goal(team, var=False)

    def throw(self, team: str) -> None:
        self._ensure_out(self.opp(team), "touch")
        spot = self.bpos(team)
        t_award = self.emit("throw_in", team, start=_pt(*spot), at=self.t + 1.0)
        t_kick = t_award + self.wait(THROW_WAIT_S, THROW_JITTER_S)
        self.seg("stoppage", t_award - 1.0, t_kick, team)
        self.t = t_kick
        self.emit("pass", team, self.pick(team, W_RESTART, spot), _pt(*spot),
                  _pt(spot[0] + self.rng.uniform(-6, 12), 34 + self.rng.uniform(-14, 14)),
                  "complete", dt=3.0)
        self.t = t_kick + 3.0

    def goalkick(self, team: str) -> None:
        self._ensure_out(self.opp(team), "byline")
        spot = (5.5, 34.0)
        t_award = self.emit("goal_kick", team, start=_pt(*spot), at=self._place_time(spot))
        t_kick = t_award + self.wait(GOAL_KICK_WAIT_S, GOAL_KICK_JITTER_S)
        self.seg("stoppage", t_award - 1.0, t_kick, team)
        self.t = t_kick
        self.emit("pass", team, self.of_pos(team, "GK"), _pt(*spot),
                  _pt(self.rng.uniform(30, 45), self.rng.uniform(15, 53)), "complete", dt=3.0)
        self.t = t_kick + 3.0

    def foulplay(self, fouler_team: str, card: bool = True) -> None:
        """Midfield foul where the ball is, optional yellow card, short free kick."""
        fouled = self.opp(fouler_team)
        self._take(fouled)
        while not FOUL_X[0] <= self.bpos(fouled)[0] <= FOUL_X[1]:
            self.shortplay(fouled)
        t0 = self.t
        fx, fy = self.bpos(fouled)
        ox, oy = _flip((fx, fy))
        fouler = self.pick(fouler_team, {"MF": 0.6, "DF": 0.4}, (ox, oy))
        t_foul = self.emit("foul", fouler_team, fouler, _pt(ox, oy))
        if card:
            self.emit("card", fouler_team, fouler, outcome="yellow", at=t_foul + 4.0)
        t_award = self.emit("free_kick", fouled, start=_pt(fx, fy), at=t_foul + 8.0)
        t_kick = t_award + self.wait(FOUL_FK_WAIT_S)
        self.seg("stoppage", t0, t_kick, fouled)
        self.label("stoppage", t_foul, t_kick, f"{fouler_team} foul, free kick")
        self.t = t_kick
        self.emit("pass", fouled, self.pick(fouled, W_RESTART, (fx, fy)), _pt(fx, fy),
                  _pt(fx + 15, fy + self.rng.uniform(-8, 8)), "complete", dt=3.0)
        self.t = t_kick + 3.0

    def _settle(self) -> None:
        """Let the team with the ball move it out of its own box before a restart run."""
        if self.poss is not None:
            self.shortplay(self.poss)

    def throws(self, n: int) -> None:
        """n throw-ins; the team that puts the ball out is random, then a little play."""
        self._settle()
        for _ in range(n):
            thrower = self.opp(self.rng.choice([HOME, AWAY]))
            self.throw(thrower)
            self.shortplay(thrower)

    def goalkicks(self, n: int) -> None:
        """n goal kicks (the other team sends the ball over the byline), then a little play."""
        self._settle()
        for _ in range(n):
            kicker = self.opp(self.rng.choice([HOME, AWAY]))
            self.goalkick(kicker)
            self.shortplay(kicker)

    def fouls(self, n: int) -> None:
        """n midfield fouls with a free kick; a few earn a yellow card."""
        self._settle()
        for _ in range(n):
            fouler = self.rng.choice([HOME, AWAY])
            self.foulplay(fouler, card=self.rng.random() < CARD_P)
            self.shortplay(self.opp(fouler))

    def sub(self, team: str) -> None:
        off_n, on_n = SUB_PLAN[team][self.sub_count[team]]
        self.sub_count[team] += 1
        off = next(p for p in self.on_pitch[team] if self.number_of[p] == off_n)
        on = self.bench[team][on_n]
        slot = self.on_pitch[team].index(off)
        self.emit("substitution", team, off, related=on)
        self.on_pitch[team][slot] = on
        self.sub_log.append((self.half, self.t, team, slot, on))
        self.seg("stoppage", self.t, self.t + SUB_STOP_S, team)
        self.label("stoppage", self.t, self.t + SUB_STOP_S, f"{team} substitution")
        self.t += SUB_STOP_S

    def injury(self, team: str) -> None:
        dur = INJURY_STOP_S + self.rng.uniform(-3, 3)
        self.emit("injury", team, self.of_pos(team, "MF"))
        self.seg("stoppage", self.t, self.t + dur, team)
        self.label("stoppage", self.t, self.t + dur, f"{team} injury stoppage")
        self.t += dur

    def water(self, at: float) -> None:
        self.t = max(self.t, at)
        self.emit("water_break_start", HOME)
        end = self.emit("water_break_end", HOME, at=self.t + WATER_BREAK_S)
        self.seg("stoppage", self.t, end, HOME)
        self.label("water_break", self.t, end, "cooling break")
        self.t = end + 1.0

    def half_end(self, at: float) -> None:
        self.t = max(self.t + 1.0, at)
        self.emit("half_end", HOME)

    def half2(self) -> None:
        self.half, self.t = 2, SECOND_HALF_START_S
        self.ball, self.poss = CENTRE, None

    def full_end(self, at: float) -> None:
        self.t = max(self.t + 1.0, at)
        self.emit("full_end", HOME)

    # .........................................................................
    # driver
    # .........................................................................

    def run(self) -> None:
        for name, *args in SCRIPT:
            if name == "counter":
                self.attack(args[0], args[1], counter=True)
            else:
                getattr(self, name)(*args)


# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
#
# Tracking (1 Hz, fixed frame)
#
# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%


def _ball_at(waypoints: list[tuple[float, tuple[float, float]]], times: list[float],
             sec: float) -> tuple[float, float]:
    """Ball position at `sec`, linear between waypoints, held outside their range."""
    i = bisect.bisect_right(times, sec)
    if i == 0:
        return waypoints[0][1]
    if i == len(waypoints):
        return waypoints[-1][1]
    (t0, p0), (t1, p1) = waypoints[i - 1], waypoints[i]
    f = 0.0 if t1 <= t0 else (sec - t0) / (t1 - t0)
    return (p0[0] + f * (p1[0] - p0[0]), p0[1] + f * (p1[1] - p0[1]))


def _tracking_rows(sim: _Sim, rng: random.Random) -> list[str]:
    """CSV rows (without header) for every match second of both halves.

    Players follow their formation spot shifted with the ball. Before each on-ball event the
    acting player runs to the ball, arriving at the event second. No player moves faster than
    MAX_SPEED_MS.
    """
    half_ends = {ev["half"]: ev["t"] for _, _, _, ev in sim.events if ev["type"] in
                 ("half_end", "full_end")}
    rows: list[str] = []
    for half, first in ((1, 0), (2, int(SECOND_HALF_START_S))):
        wps = sorted((w for w in sim.waypoints if w[0] == half), key=lambda w: w[1])
        times = [w[1] for w in wps]
        wp = [(w[1], w[2]) for w in wps]
        kind_at: dict[int, tuple[str, str]] = {}
        for h, t0, t1, kind, team in sim.segments:
            if h == half:
                for s in range(int(t0), int(t1) + 1):
                    kind_at[s] = (kind, team)
        actor_secs: dict[str, list[tuple[int, tuple[float, float]]]] = {}
        for h, t, _, ev in sorted(sim.events, key=lambda e: (e[0], e[1], e[2])):
            if h == half and ev["player_id"] and "start" in ev:
                spot = sim._fixed(ev["team"], tuple(ev["start"]))
                actor_secs.setdefault(ev["player_id"], []).append((int(t), spot))
        slots = {tm: list(sim.initial_slots[tm]) for tm in (HOME, AWAY)}
        for h, t_sub, tm, slot, new in sorted(sim.sub_log):
            if h < half:
                slots[tm][slot] = new
        pending = [s for s in sorted(sim.sub_log) if s[0] == half]
        pos: dict[tuple[str, int], tuple[float, float]] = {}
        for sec in range(first, int(half_ends[half]) + 1):
            while pending and pending[0][1] <= sec:
                _, _, tm, slot, new = pending.pop(0)
                slots[tm][slot] = new
            kind = kind_at.get(sec, ("calm", HOME))[0]
            sigma, alpha, cap = TRACK_KINDS[kind]
            bx, by = _ball_at(wp, times, sec)
            for tm in (HOME, AWAY):
                bt = (bx, by) if tm == HOME else _flip((bx, by))
                for slot, pid in enumerate(slots[tm]):
                    base = SLOT_BASE[slot]
                    tx = base[0] + SLOT_BALL_PULL[slot] * (bt[0] - CENTRE[0])
                    ty = base[1] + LATERAL_PULL * (bt[1] - CENTRE[1])
                    tx, ty = (tx, ty) if tm == HOME else _flip((tx, ty))
                    cur = pos.get((tm, slot))
                    secs = actor_secs.get(pid, [])
                    i = bisect.bisect_left(secs, (sec, (-1.0, -1.0)))
                    if cur is None:  # first frame of the half: the kickoff taker starts on the ball
                        soon = i < len(secs) and secs[i][0] - sec <= KICKOFF_LEAD_S
                        cur = secs[i][1] if soon else (tx, ty)
                    else:
                        dx = _clamp(alpha * (tx - cur[0]), -cap, cap) + rng.gauss(0, sigma)
                        dy = _clamp(alpha * (ty - cur[1]), -cap, cap) + rng.gauss(0, sigma)
                        if i < len(secs) and secs[i][0] - sec <= LEAD_S:
                            # run to the event spot so as to be on it at the event second
                            (te, (ex, ey)) = secs[i]
                            n = max(te - sec, 1)
                            dx, dy = (ex - cur[0]) / n, (ey - cur[1]) / n
                            run = math.hypot(dx, dy)
                            if run > ACTOR_RUN_MS:
                                dx, dy = dx * ACTOR_RUN_MS / run, dy * ACTOR_RUN_MS / run
                        step = math.hypot(dx, dy)
                        if step > MAX_SPEED_MS:
                            dx, dy = dx * MAX_SPEED_MS / step, dy * MAX_SPEED_MS / step
                        cur = (cur[0] + dx, cur[1] + dy)
                    cur = (_clamp(cur[0], 0.0, PITCH_LENGTH_M), _clamp(cur[1], 0.0, PITCH_WIDTH_M))
                    pos[(tm, slot)] = cur
                    rows.append(f"{sec},{half},{pid},{tm},{cur[0]:.2f},{cur[1]:.2f}")
            rows.append(f"{sec},{half},ball,,{_clamp(bx, 0, PITCH_LENGTH_M):.2f},"
                        f"{_clamp(by, 0, PITCH_WIDTH_M):.2f}")
    return rows


# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
#
# Output
#
# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

README_TEXT = """# sim-placeholder (PLACEHOLDER)

**PLACEHOLDER, generated by code, replace with the teammate's dataset.**
Do not edit by hand. Regenerate with `python -m backend.ingest.sim_placeholder` (seed 42).

Fictional match `nbr-cal-seed42`: Northbridge FC (`NBR`, home) 2-1 Calder Town (`CAL`).

## Files

- `roster.json`: `match_id`, `home`, `away`, club names and `players[]` with `player_id`,
  `name`, `team`, `position` (GK/DF/MF/FW), `number`, `starter`. 11 starters and 3 subs per club.
- `events.jsonl`: one JSON event per line, shape of `contracts/event.schema.json`.
  Ids `e0001`... in order.
- `tracking.csv`: `t,half,player_id,team,x,y`. One row per player per match second plus a
  `ball` row (`team` empty).
- `labels.json`: scripted ground truth, `{kind, half, t_start, t_end, note}` with kind `calm`,
  `attack`, `stalled`, `set_piece_wait`, `stoppage` or `water_break`.

## Units, frames and clock

- Metres on a 105 x 68 pitch, `t` in seconds.
- Events use the acting team's frame (it attacks left to right). Tracking uses the FIXED
  frame: NBR attacks left to right for the whole match, no end swap at half time.
- The first half runs past 2700 s (stoppage time, `half_end` at 2790 s). The second half
  starts at `t = 2700` and `full_end` is at 5580 s. **So t values 2700 to 2790 occur in both
  halves. Always key by (half, t).**
- Tracking has a frame for every second of play, including stoppages and the two 90 s water
  breaks (about 22:00 and 67:00). No player moves faster than 9.5 m/s and the ball never
  moves faster than 35 m/s between two frames.
- The acting player of an on-ball event stands on the ball in the tracking frame of that
  second. Every change of possession is explained by an event (interception, tackle, out,
  restart or kickoff).
- Restart events (`corner`, `free_kick`, `penalty`, `throw_in`, `goal_kick`) mark the moment
  the restart is awarded. The taker's next pass or shot is the kick, 5 to 60 s later.
"""


def generate(out_dir: Path = DEFAULT_OUT_DIR, seed: int = SEED) -> dict:
    """Write the placeholder dataset to `out_dir` and return a small summary.

    Args:
        out_dir: Target folder (created if missing).
        seed: RNG seed; the same seed gives byte-identical files.

    Returns:
        Dict with event count and label counts by kind.
    """
    sim = _Sim(seed)
    sim.run()
    out_dir.mkdir(parents=True, exist_ok=True)
    ordered = sorted(sim.events, key=lambda e: (e[0], e[1], e[2]))
    lines = []
    for i, (_, _, _, ev) in enumerate(ordered, start=1):
        lines.append(json.dumps({"id": f"e{i:04d}", **ev}))
    (out_dir / "events.jsonl").write_text("\n".join(lines) + "\n")
    (out_dir / "roster.json").write_text(json.dumps(_roster_json(), indent=2) + "\n")
    labels = sorted(sim.labels, key=lambda lb: (lb["half"], lb["t_start"]))
    (out_dir / "labels.json").write_text(json.dumps(labels, indent=1) + "\n")
    rows = _tracking_rows(sim, random.Random(seed + 1))
    (out_dir / "tracking.csv").write_text("t,half,player_id,team,x,y\n" + "\n".join(rows) + "\n")
    (out_dir / "README.md").write_text(README_TEXT)
    counts: dict[str, int] = {}
    for lb in labels:
        counts[lb["kind"]] = counts.get(lb["kind"], 0) + 1
    return {"events": len(lines), "labels": counts}


def main() -> int:
    parser = argparse.ArgumentParser(description="Write the PLACEHOLDER simulated match dataset.")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT_DIR, help="output folder")
    args = parser.parse_args()
    print(json.dumps(generate(args.out)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
