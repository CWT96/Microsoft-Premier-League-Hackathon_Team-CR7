"""detector.py — stage [1]: turns events (and optional tracking) into one state per second.

Last Modified: 2026-10-24 23:20, first version: dull score, attacks, situations, restarts, breaks
Inputs:  ``Match`` events (and tracking frames when present), ``backend/detect/xt_grid.py``
Outputs: ``DetectorState`` per match second (``Detector.step`` / ``Detector.run``)

Workflow:
  - Each on-ball event is kept in a rolling window (acting team's frame) for the dull sub-scores.
  - Restart awards stay pending until the taker's next on-ball event; that event is the kick.
  - An attack starts when the ball is in the final third, xT is high or a shot is taken, and ends
    on a shot, goal, save, dead ball, 5 s back out of the final third, or a 10 s stall.
  - Tracking (fixed frame, home attacks left to right) feeds movement and pressure only.

CAUTION: pure code, no LLM. Durations use one continuous clock (the second half is shifted by the
first-half stoppage time); ``last_attack_end_t`` is reported in the displayed clock.
"""

from __future__ import annotations

import logging
import math
from collections import defaultdict, deque
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from typing import Any

from backend.core.constants import GOAL_CENTRE, RESTART_TYPES, SECOND_HALF_START_S
from backend.core.geometry import flip_xy, in_final_third, zone_of
from backend.core.types import (
    BallZone,
    BreakKind,
    DetectorState,
    Match,
    MatchEvent,
    Mode,
    Roster,
    Situation,
    TrackingFrame,
)
from backend.detect.xt_grid import xt

# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
#
# Thresholds
#
# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

WINDOW_S = 25.0  # rolling window of the dull sub-scores
THREAT_WINDOW_S = 10.0
PASS_RATE_ZERO_PER_MIN = 4.0
PASS_RATE_FULL_PER_MIN = 10.0
LOW_THREAT_FULL_XT = 0.05  # low_threat = 1 below this
LOW_THREAT_ZERO_XT = 0.15  # low_threat = 0 from here
SHOT_GAP_FULL_S = 180.0
POSSESSION_FULL_PASSES = 6
MOVEMENT_WINDOW_S = 3.0
MOVEMENT_FULL_MPS = 3.0
ATTACK_ENTRY_XT = 0.15
BACK_IN_MIDDLE_EXIT_S = 5.0
STALL_S = 10.0
STALL_MOVEMENT = 0.55
PROGRESS_M = 2.0  # forward ball progress that counts as progress when there is no tracking
REFEREE_HOLD_S = 6.0
KICKOFF_HOLD_S = 6.0
WATER_TIMEOUT_S = 120.0  # a water break with no end event is closed by the next on-ball event
NEAR_BOX_MIN_X = 75.0
NEAR_BOX_MAX_DY = 25.0

LOGGER = logging.getLogger(__name__)
WON_OUTCOMES = frozenset({"won", None})

ON_BALL_TYPES = frozenset({"pass", "carry", "shot", "save", "clearance", "tackle", "interception"})
REFEREE_TYPES = frozenset({"foul", "card", "var_check"})
STOP_TYPES = frozenset({"substitution", "injury"})  # stay active until play resumes


def _ramp(value: float, lo: float, hi: float) -> float:
    """0 at or below ``lo``, 1 at or above ``hi``, linear between."""
    return min(max((value - lo) / (hi - lo), 0.0), 1.0)


@dataclass
class _Rec:
    """One on-ball event in the rolling window (acting team's frame)."""

    clock: float
    type: str
    x: float
    xt: float


@dataclass
class _Flags:
    """What happened in the current second."""

    shot: bool = False
    goal: bool = False
    save: bool = False
    dead: bool = False  # foul, card, out, restart award, stoppage, break: the ball is dead
    restart_taken: bool = False
    max_xt: float = 0.0
    poss_change: bool = False
    taken_detail: dict[str, Any] = field(default_factory=dict)


class Detector:
    """Stateful per-second detector. Feed it seconds in (half, t) order.

    Args:
        roster: Match roster (kept for callers that build the detector from a ``Match``).
        home: Home team code. Tracking is in the fixed frame where home attacks left to right.
        away: Away team code.
    """

    def __init__(self, roster: Roster, home: str, away: str) -> None:
        self._roster = roster
        self._home = home
        self._away = away
        self._half: int | None = None
        self._h1_last = 0.0
        self._score = {home: 0, away: 0}
        self._goals: dict[str, str] = {}  # goal event id -> scoring team, for amends
        self._last_shot_clock = 0.0
        self._last_attack_end_t: float | None = None
        self._start_half(1)

    # .........................................................................
    # Public API
    # .........................................................................

    def lobby_state(self) -> DetectorState:
        """The single state before kickoff."""
        return self._build_state(
            half=1, t=0.0, mode="lobby", break_kind=None, situation="kickoff", parts=self._parts(0),
            threat=0.0, movement=None, pressure=None, ball_xy=None, zone=None, new_events=(),
            detail={}, restart_taken=False, ssh=0.0)

    def run(self, match: Match) -> Iterator[DetectorState]:
        """Replay a whole match through the detector.

        Args:
            match: Loaded match; tracking is used when present.

        Yields:
            One lobby state, then one state per second of both halves (stoppage included),
            ending with the full-time state.
        """
        yield self.lobby_state()
        buckets: dict[tuple[int, int], list[MatchEvent]] = defaultdict(list)
        for ev in match.events:
            buckets[(ev.half, int(ev.t))].append(ev)
        spans = ((1, 0, int(match.first_half_end_t)),
                 (2, int(SECOND_HALF_START_S), int(match.full_end_t)))
        for half, first, last in spans:
            for sec in range(first, last + 1):
                frame = match.tracking.at(half, sec) if match.tracking else None
                yield self.step(half, float(sec), buckets.get((half, sec), ()), frame)

    def step(self, half: int, t: float, new_events: Iterable[MatchEvent] = (),
             tracking_frame: TrackingFrame | None = None) -> DetectorState:
        """Advance one match second.

        Args:
            half: 1 or 2.
            t: Displayed clock of the second (whole number).
            new_events: Events whose ``t`` falls in this second.
            tracking_frame: Positions for this second, or None.

        Returns:
            The detector state for this second.
        """
        if half != self._half:
            self._start_half(half)
        if half == 1:
            self._h1_last = max(self._h1_last, t + 1.0)
        clock = self._clock(half, t)
        events = tuple(sorted(new_events, key=lambda e: (e.t, e.id)))
        flags = _Flags()
        for ev in events:
            self._apply_event(ev, self._clock(half, ev.t), flags)

        movement, pressure, ball_track = self._tracking_signals(clock, tracking_frame)
        ball_xy = self._ball_position(ball_track)
        zone = zone_of(ball_xy[0]) if ball_xy is not None else None
        dead = self._is_dead(clock)
        self._update_attack(t, clock, zone, ball_xy, movement, tracking_frame is not None,
                            dead, flags)
        threat = self._threat(clock)
        ssh = clock - self._last_shot_clock
        situation, detail = self._situation(clock, events, flags)
        return self._build_state(
            half=half, t=t, mode=self._mode(), break_kind=self._break_kind(), situation=situation,
            parts=self._parts(clock), threat=threat, movement=movement, pressure=pressure,
            ball_xy=ball_xy, zone=zone, new_events=events, detail=detail,
            restart_taken=flags.restart_taken, ssh=ssh)

    # .........................................................................
    # State
    # .........................................................................

    def _start_half(self, half: int) -> None:
        """Reset everything that cannot carry across the half-time break."""
        self._half = half
        self._possession: str | None = None
        self._poss_passes = 0
        self._ball_ev: tuple[tuple[float, float], str] | None = None
        self._water_since = 0.0
        self._records: deque[_Rec] = deque()
        self._frames: deque[tuple[float, dict[str, tuple[float, float]]]] = deque()
        self._attack = False
        self._stalled = False
        self._back_mid_since: float | None = None
        self._stall_ref: float | None = None
        self._max_x = 0.0
        self._restart: dict[str, Any] | None = None
        self._referee: tuple[float, dict[str, Any]] | None = None  # (until clock, detail)
        self._stop: tuple[str, dict[str, Any]] | None = None  # substitution / injury
        self._kickoff_until: float | None = None
        self._after_goal: str | None = None
        self._water = False
        self._half_time = False
        self._full_time = False

    def _clock(self, half: int, t: float) -> float:
        """Continuous seconds: the second half is shifted by the first-half stoppage time."""
        return t if half == 1 else t + max(0.0, self._h1_last - SECOND_HALF_START_S)

    def _frame_of(self, team: str) -> bool:
        """True when ``team`` attacks right to left in the fixed tracking frame."""
        return team == self._away

    def _touch(self, team: str, is_pass: bool, flags: _Flags) -> None:
        if team != self._possession:
            self._poss_passes = 0
            flags.poss_change = self._possession is not None
        self._possession = team
        if is_pass:
            self._poss_passes += 1

    def _set_ball(self, xy: tuple[float, float] | None, team: str) -> None:
        if xy is not None:
            self._ball_ev = (xy, team)

    def _apply_event(self, ev: MatchEvent, c: float, flags: _Flags) -> None:
        typ = ev.type
        old_goal_team = self._goals.pop(ev.amends, None) if ev.amends else None
        if old_goal_team is not None:  # a corrected or overturned goal is not counted twice
            self._score[old_goal_team] -= 1
        if typ in ON_BALL_TYPES:
            self._on_ball(ev, c, flags)
        elif typ == "goal":
            self._score[ev.team] = self._score.get(ev.team, 0) + 1
            self._goals[ev.id] = ev.team
            flags.goal = flags.dead = True
            self._last_attack_end_t = ev.t
            self._after_goal = ev.team
            self._restart = self._referee = None
            self._poss_passes = 0
            self._set_ball(ev.start, ev.team)
        elif typ == "kickoff":
            self._after_goal = None
            self._restart = None
            self._kickoff_until = c + KICKOFF_HOLD_S
            self._possession, self._poss_passes = ev.team, 0
            self._set_ball(ev.start, ev.team)
        elif typ in RESTART_TYPES:
            self._restart = {"restart": typ, "team": ev.team,
                             "x": ev.start[0] if ev.start else None,
                             "near_box": self._near_box(ev)}
            self._referee = None
            self._possession, self._poss_passes = ev.team, 0
            self._set_ball(ev.start, ev.team)
            flags.dead = True
        elif typ in REFEREE_TYPES:
            self._referee = (c + REFEREE_HOLD_S, {"kind": typ})
            flags.dead = True
        elif typ == "out":
            flags.dead = True
        elif typ in STOP_TYPES:
            detail: dict[str, Any] = {"player_id": ev.player_id}
            if ev.related_player_id:
                detail["related_player_id"] = ev.related_player_id
            self._stop = (typ, detail)
            flags.dead = True
        elif typ == "water_break_start":
            self._water = flags.dead = True
            self._water_since = c
        elif typ == "water_break_end":
            self._water = False
        elif typ == "half_end" and ev.half == 1:
            self._half_time = flags.dead = True
        elif typ == "full_end":
            self._full_time = flags.dead = True

    def _on_ball(self, ev: MatchEvent, c: float, flags: _Flags) -> None:
        pos = ev.start or ev.end
        pts = [p for p in (ev.start, ev.end) if p is not None]
        value = max((xt(*p) for p in pts), default=0.0)
        if pos is not None:
            self._records.append(_Rec(c, ev.type, pos[0], value))
        flags.max_xt = max(flags.max_xt, value)
        if ev.type == "tackle" and ev.outcome not in WON_OUTCOMES:
            LOGGER.info("tackle %s outcome %r: possession unchanged", ev.id, ev.outcome)
        else:
            self._touch(ev.team, ev.type == "pass", flags)
            self._set_ball(ev.end or ev.start, ev.team)
        if self._water and c - self._water_since >= WATER_TIMEOUT_S:
            LOGGER.warning("water break at clock %.0f has no end event; closed by play", c)
            self._water = False
        self._after_goal = None
        self._referee = None
        if self._restart is not None:
            flags.restart_taken = True
            flags.taken_detail = dict(self._restart)
            self._restart = None
        self._stop = None
        self._kickoff_until = None
        if ev.type == "shot":
            flags.shot = True
            self._last_shot_clock = c
        elif ev.type == "save":
            flags.save = True

    @staticmethod
    def _near_box(ev: MatchEvent) -> bool:
        if ev.start is None:
            return False
        if ev.type == "penalty":
            return True
        return (ev.start[0] >= NEAR_BOX_MIN_X
                and (ev.type == "corner" or abs(ev.start[1] - GOAL_CENTRE[1]) <= NEAR_BOX_MAX_DY))

    # .........................................................................
    # Signals
    # .........................................................................

    def _tracking_signals(
        self, clock: float, frame: TrackingFrame | None
    ) -> tuple[float | None, float | None, tuple[float, float] | None]:
        """Movement, pressure and ball position from one tracking frame.

        Args:
            clock: Continuous clock of this second.
            frame: Tracking frame (fixed frame), or None.

        Returns:
            (movement 0-1, pressure in metres, ball in the fixed frame). Players or a ball with
            non-finite coordinates are ignored, so each value can be None.
        """
        if frame is None:
            self._frames.clear()
            return None, None, None
        players = [p for p in frame.players if math.isfinite(p[2]) and math.isfinite(p[3])]
        ball = frame.ball if frame.ball and all(math.isfinite(v) for v in frame.ball) else None
        self._frames.append((clock, {pid: (x, y) for pid, _, x, y in players}))
        while self._frames and self._frames[0][0] < clock - MOVEMENT_WINDOW_S:
            self._frames.popleft()
        speeds: list[float] = []
        pairs = list(self._frames)
        for (c0, p0), (c1, p1) in zip(pairs, pairs[1:]):
            for pid, (x1, y1) in p1.items():
                if pid in p0 and c1 > c0:
                    speeds.append(((x1 - p0[pid][0]) ** 2 + (y1 - p0[pid][1]) ** 2) ** 0.5
                                  / (c1 - c0))
        movement = (min(sum(speeds) / len(speeds) / MOVEMENT_FULL_MPS, 1.0) if speeds else None)
        pressure = None
        if ball is not None and self._possession is not None:
            dists = [((x - ball[0]) ** 2 + (y - ball[1]) ** 2) ** 0.5
                     for _, team, x, y in players if team != self._possession]
            pressure = min(dists) if dists else None
        return movement, pressure, ball

    def _ball_position(self, ball_track: tuple[float, float] | None) -> tuple[float, float] | None:
        """Ball in the possession team's attacking frame (tracking first, else last event)."""
        if self._possession is None:
            return None
        if ball_track is not None:
            return flip_xy(*ball_track) if self._frame_of(self._possession) else ball_track
        if self._ball_ev is None:
            return None
        xy, team = self._ball_ev
        return xy if team == self._possession else flip_xy(*xy)

    def _threat(self, clock: float) -> float:
        lo = clock + 1.0 - THREAT_WINDOW_S
        return max((r.xt for r in self._records if r.clock >= lo), default=0.0)

    def _parts(self, clock: float) -> dict[str, float]:
        lo = clock + 1.0 - WINDOW_S
        while self._records and self._records[0].clock < lo:
            self._records.popleft()
        recs = list(self._records)
        passes = sum(1 for r in recs if r.type == "pass")
        max_xt = max((r.xt for r in recs), default=0.0)
        return {
            "pass_rate": _ramp(passes * 60.0 / WINDOW_S, PASS_RATE_ZERO_PER_MIN,
                               PASS_RATE_FULL_PER_MIN),
            "not_final_third": (sum(1 for r in recs if not in_final_third(r.x)) / len(recs)
                                if recs else 0.0),
            "low_threat": 1.0 - _ramp(max_xt, LOW_THREAT_FULL_XT, LOW_THREAT_ZERO_XT),
            "shot_gap": min(max(clock - self._last_shot_clock, 0.0) / SHOT_GAP_FULL_S, 1.0),
            "possession": min(self._poss_passes / POSSESSION_FULL_PASSES, 1.0),
        }

    # .........................................................................
    # Attacks
    # .........................................................................

    def _is_dead(self, clock: float) -> bool:
        """True when play is not live, so no attack can start or stall."""
        referee = self._referee is not None and clock < self._referee[0]
        return (self._full_time or self._half_time or self._water or self._after_goal is not None
                or self._stop is not None or self._restart is not None or referee)

    def _update_attack(self, t: float, clock: float, zone: BallZone | None,
                       ball_xy: tuple[float, float] | None, movement: float | None,
                       has_tracking: bool, dead: bool, flags: _Flags) -> None:
        in_final = zone == "final_third"
        if self._stalled and self._latch_released(in_final, ball_xy, movement, flags):
            self._stalled = False
        exited = False
        if self._attack:
            if flags.shot or flags.goal or flags.save or flags.dead or dead:
                exited = True
            elif in_final:
                self._back_mid_since = None
            else:
                if self._back_mid_since is None:
                    self._back_mid_since = clock
                exited = clock - self._back_mid_since >= BACK_IN_MIDDLE_EXIT_S
                self._stall_ref = None
            if not exited and in_final and ball_xy is not None:
                exited = self._stall_check(clock, ball_xy[0], movement, has_tracking)
                if exited:
                    self._stalled = True
            if exited:
                self._attack = False
                if not flags.goal:
                    self._last_attack_end_t = t
        if not self._attack and not exited and not dead and not self._stalled:
            if in_final or flags.shot or flags.max_xt > ATTACK_ENTRY_XT:
                self._attack = True
                self._back_mid_since = None
                self._stall_ref = clock
                self._max_x = ball_xy[0] if ball_xy is not None else 0.0

    def _latch_released(self, in_final: bool, ball_xy: tuple[float, float] | None,
                        movement: float | None, flags: _Flags) -> bool:
        """A stall never blocks a real chance: any sign of life lifts the latch."""
        fast = movement is not None and movement >= STALL_MOVEMENT
        progress = (movement is None and ball_xy is not None
                    and ball_xy[0] > self._max_x + PROGRESS_M)
        return (not in_final or flags.shot or flags.dead or fast or progress
                or flags.max_xt > ATTACK_ENTRY_XT or flags.poss_change)

    def _stall_check(self, clock: float, x: float, movement: float | None,
                     has_tracking: bool) -> bool:
        """Ball still in the final third, little movement or progress for STALL_S."""
        if has_tracking and movement is not None:
            if movement >= STALL_MOVEMENT:
                self._stall_ref = None
            elif self._stall_ref is None:
                self._stall_ref = clock
        else:
            if x > self._max_x + PROGRESS_M or self._stall_ref is None:
                self._max_x, self._stall_ref = max(x, self._max_x), clock
        return self._stall_ref is not None and clock - self._stall_ref >= STALL_S

    # .........................................................................
    # Output
    # .........................................................................

    def _mode(self) -> Mode:
        if self._full_time:
            return "full_time"
        if self._half_time or self._water:
            return "break"
        return "attack" if self._attack else "live"

    def _break_kind(self) -> BreakKind | None:
        if self._full_time:
            return None
        if self._half_time:
            return "half_time"
        return "water_break" if self._water else None

    def _situation(self, clock: float, events: tuple[MatchEvent, ...],
                   flags: _Flags) -> tuple[Situation, dict[str, Any]]:
        if self._full_time:
            return "full_time", {}
        if self._half_time:
            return "half_time", {}
        if self._water:
            return "water_break", {}
        if self._after_goal is not None:
            return "after_goal", {"team": self._after_goal}
        if self._stop is not None:
            return self._stop[0], dict(self._stop[1])  # type: ignore[return-value]
        if self._restart is not None:
            return self._restart["restart"], dict(self._restart)
        if self._referee is not None and clock < self._referee[0]:
            return "referee_decision", dict(self._referee[1])
        detail = dict(flags.taken_detail)
        if self._kickoff_until is not None and clock < self._kickoff_until:
            return "kickoff", detail
        if self._stalled:
            return "stalled_near_box", detail
        return ("attack" if self._attack else "open_play"), detail

    def _build_state(self, *, half: int, t: float, mode: Mode, break_kind: BreakKind | None,
                     situation: Situation, parts: dict[str, float], threat: float,
                     movement: float | None, pressure: float | None,
                     ball_xy: tuple[float, float] | None, zone: BallZone | None,
                     new_events: tuple[MatchEvent, ...], detail: dict[str, Any],
                     restart_taken: bool, ssh: float) -> DetectorState:
        return DetectorState(
            t=t, half=half, mode=mode, break_kind=break_kind,
            score_home=self._score[self._home], score_away=self._score[self._away],
            situation=situation, dull_score=sum(parts.values()) / len(parts), threat=threat,
            movement=movement, pressure_m=pressure, ball_xy=ball_xy, ball_zone=zone,
            possession_team=self._possession, seconds_since_shot=ssh,
            last_attack_end_t=self._last_attack_end_t, attack_active=self._attack,
            restart_pending=self._restart is not None, restart_taken=restart_taken,
            new_events=new_events, situation_detail=detail, dull_parts=parts)
