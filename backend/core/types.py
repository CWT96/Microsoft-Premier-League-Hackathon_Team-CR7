"""Internal types shared by every backend stage.

Payloads that cross the WebSocket live in ``backend/app/models.py`` (mirrors of ``contracts/``).
The types here are internal: they flow between ingest → detector → timing → generator →
resolver → scoring inside one process. ``to_contract()`` turns the ones that are published
into contract-shaped dicts.

Dict-valued fields are excluded from ``==`` and ``hash`` (``_dict_field``) so frozen objects stay
hashable. **Never mutate them**: one ``Moment`` is shared by every user.

Clock: ``t`` is the displayed match clock in seconds. The second half starts at t = 2700
even after first-half stoppage time, so always order by ``(half, t)`` (see ``clock_key``).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from backend.core.constants import EVENT_TYPES

Position = Literal["GK", "DF", "MF", "FW"]
Mode = Literal["lobby", "live", "attack", "break", "full_time"]
BreakKind = Literal["half_time", "water_break"]
BallZone = Literal["own_third", "middle_third", "final_third"]

# What the detector says is happening on the pitch.
Situation = Literal[
    "kickoff", "open_play", "stalled_near_box", "attack",
    "throw_in", "goal_kick", "corner", "free_kick", "penalty",
    "referee_decision", "substitution", "injury", "water_break",
    "after_goal", "half_time", "full_time",
]

# What the timing agent hands the generator. Describes the match, never a question.
MomentSituation = Literal[
    "calm_play", "stalled_near_box", "throw_in", "goal_kick", "set_piece_wait",
    "referee_decision", "substitution", "injury", "water_break", "after_goal",
    "kickoff", "half_time",
]
MomentKind = Literal["instant", "kickoff_stack", "ht_stack", "water_break", "player_instant"]
Category = Literal["instant", "forecast", "player"]
LockRule = Literal["fixed", "on_restart"]

TimingAction = Literal["open", "hold", "blocked", "scheduled", "silent", "calm"]
TimingSource = Literal["llm", "rule", "rule_fallback", "schedule"]


def _dict_field() -> Any:
    """A dict field left out of equality and hashing (treat as read-only)."""
    return field(default_factory=dict, hash=False, compare=False)


def player_qid(moment_id: str, player_id: str) -> str:
    """qid of a player moment, which differs per user: ``<moment id>-<player_id>``."""
    return f"{moment_id}-{player_id}"


def clock_key(half: int, t: float) -> tuple[int, float]:
    """Sort key for match time: first by half, then by displayed clock."""
    return (half, t)


# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
#
# Match data ([0] ingest)
#
# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%


@dataclass(frozen=True)
class Player:
    player_id: str
    name: str
    team: str
    position: Position
    number: int
    starter: bool = True


@dataclass(frozen=True)
class Roster:
    home: str
    away: str
    home_name: str
    away_name: str
    players: tuple[Player, ...]
    _index: dict[str, Player] = field(init=False, repr=False, hash=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_index", {p.player_id: p for p in self.players})

    def by_id(self) -> dict[str, Player]:
        """Player lookup (cached; do not mutate)."""
        return self._index

    def team_players(self, team: str) -> tuple[Player, ...]:
        return tuple(p for p in self.players if p.team == team)

    def opponent(self, team: str) -> str:
        if team == self.home:
            return self.away
        if team == self.away:
            return self.home
        raise ValueError(f"unknown team {team!r}")


@dataclass(frozen=True)
class MatchEvent:
    """One event, same fields as ``contracts/event.schema.json``.

    Coordinates are per acting team: the acting team attacks left to right.
    """

    id: str
    t: float
    half: int
    team: str
    type: str
    player_id: str | None = None
    start: tuple[float, float] | None = None
    end: tuple[float, float] | None = None
    outcome: str | None = None
    amends: str | None = None
    related_player_id: str | None = None

    def __post_init__(self) -> None:
        if self.type not in EVENT_TYPES:
            raise ValueError(f"unknown event type {self.type!r}")

    def to_contract(self) -> dict[str, Any]:
        """Contract dict; drops absent optional fields (start/end may not be null)."""
        out: dict[str, Any] = {"id": self.id, "t": self.t, "half": self.half, "team": self.team,
                               "type": self.type, "player_id": self.player_id}
        for k in ("start", "end"):
            v = getattr(self, k)
            if v is not None:
                out[k] = list(v)
        for k in ("outcome", "amends", "related_player_id"):
            v = getattr(self, k)
            if v is not None:
                out[k] = v
        return out


@dataclass(frozen=True)
class TrackingFrame:
    """Positions at one match second, in the FIXED frame (home attacks left to right)."""

    t: float
    half: int
    players: tuple[tuple[str, str, float, float], ...]  # (player_id, team, x, y)
    ball: tuple[float, float] | None

    def to_contract(self) -> dict[str, Any]:
        return {
            "t": self.t,
            "half": self.half,
            "players": [
                {"player_id": pid, "team": team, "x": x, "y": y}
                for pid, team, x, y in self.players
            ],
            "ball": list(self.ball) if self.ball is not None else None,
        }


@dataclass(frozen=True)
class Tracking:
    frames: dict[tuple[int, int], TrackingFrame] = field(hash=False, compare=False)  # (half, int(t))

    def at(self, half: int, t: float) -> TrackingFrame | None:
        return self.frames.get((half, int(t)))


@dataclass(frozen=True)
class Match:
    match_id: str
    roster: Roster
    events: tuple[MatchEvent, ...]  # sorted by (half, t, id)
    tracking: Tracking | None  # optional input; everything must work without it
    first_half_end_t: float  # displayed clock of the half_end event (≥ 2700)
    full_end_t: float  # displayed clock of the full_end event (≥ 5400)


# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
#
# Detector output ([1])
#
# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%


@dataclass(frozen=True)
class DetectorState:
    """What the detector reports once per match second. Raw signals only.

    The timing agent turns these into the interruption cost; the detector does not.
    Tracking-derived fields (``movement``, ``pressure_m``) are ``None`` without tracking.
    """

    t: float
    half: int
    mode: Mode
    break_kind: BreakKind | None
    score_home: int
    score_away: int
    situation: Situation
    dull_score: float  # pipeline.md §[1] mean of sub-scores, 0–1
    threat: float  # max xT in the last 10 s, 0–1
    movement: float | None  # mean player speed over 3 s, 0 m/s → 0, ≥ 3 m/s → 1
    pressure_m: float | None  # ball carrier to nearest opponent, metres
    ball_xy: tuple[float, float] | None  # in the possession team's attacking frame
    ball_zone: BallZone | None  # relative to the possession team
    possession_team: str | None
    seconds_since_shot: float  # since the last shot, or since kickoff if none yet
    last_attack_end_t: float | None
    attack_active: bool
    restart_pending: bool  # a restart was awarded and has not been taken yet
    restart_taken: bool  # the pending restart was taken in this second
    new_events: tuple[MatchEvent, ...] = ()  # events that arrived in this second
    situation_detail: dict[str, Any] = _dict_field()  # e.g. {"restart": "corner", "team": "NBR"}
    dull_parts: dict[str, float] = _dict_field()  # pass_rate, not_final_third, low_threat, ...


# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
#
# Timing output ([2b])
#
# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%


@dataclass(frozen=True)
class Moment:
    """An order to the generator: 'the match can spare attention now'.

    Timing decides WHEN and WHY. The generator decides WHAT (template, params, text).
    ``moment_id`` becomes the question ``qid``; every user who picks it up sees the same
    question (player moments add the player id).
    """

    moment_id: str
    kind: MomentKind
    category: Category
    open_at: float
    lock_at: float
    lock_rule: LockRule
    situation: MomentSituation
    cost: float
    half: int
    tone: str | None = None
    situation_detail: dict[str, Any] = _dict_field()


@dataclass(frozen=True)
class TimingDecision:
    """Why timing opened, held or blocked; published as ``timing.decision``."""

    t: float
    half: int
    action: TimingAction
    source: TimingSource
    reason: str
    moment_ids: tuple[str, ...] = ()
    situation: str | None = None
    tone: str | None = None
    blocked_by: str | None = None
    inputs: dict[str, Any] = _dict_field()  # must contain "cost"

    def __post_init__(self) -> None:
        if "cost" not in self.inputs:
            raise ValueError("TimingDecision.inputs must contain 'cost'")
        if len(self.reason) > 200 or (self.tone is not None and len(self.tone) > 40):
            raise ValueError("reason ≤ 200 and tone ≤ 40 characters (contract)")

    def to_contract(self) -> dict[str, Any]:
        d = asdict(self)
        d["moment_ids"] = list(self.moment_ids)
        return d


@dataclass(frozen=True)
class TimingOutput:
    moments: tuple[Moment, ...] = ()
    decisions: tuple[TimingDecision, ...] = ()


# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
#
# Resolver output ([4]) and answers ([6])
#
# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%


@dataclass(frozen=True)
class Outcome:
    """What code decided happened. Shared by every user who answered ``qid``."""

    qid: str
    status: Literal["resolved", "void"]
    correct_option: str | None
    evidence: tuple[str, ...]
    resolved_at: float | None = None
    facts: dict[str, Any] = _dict_field()


@dataclass(frozen=True)
class Answer:
    """One user's interaction with one card they were shown.

    Scoring joins it to the shared ``Outcome`` by ``qid``. ``answer_latency_s`` is measured
    from the moment the card reached this user.
    """

    qid: str
    category: Category
    situation: MomentSituation
    open_at: float
    lock_at: float
    half: int
    option: str | None  # None = shown but not answered
    answer_latency_s: float | None
    used_hint: bool = False
    seq: int = 0  # delivery order for this user (0, 1, 2, ...); scoring orders by it first
