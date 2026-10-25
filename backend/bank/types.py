"""Data types shared by the question bank."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from backend.resolve.primitives import Ctx

Kind = Literal["instant", "window", "player"]
Phase = Literal["kickoff", "half_time"]
CATEGORY = {"instant": "instant", "window": "forecast", "player": "player"}


@dataclass(frozen=True)
class Template:
    """One row of the bank (templates.json)."""
    id: str
    kind: Kind
    text: str
    options: list[str]
    params: dict[str, list | str]  # a list of allowed values, or "tuned" (set by the model)
    counts_from: Literal["lock", "half_start", "kickoff"]
    resolve: dict
    prob: str
    rare: bool
    positions: list[str] | None
    guards: list[str]
    teaches: str
    team_from: Literal["none", "possession", "trailing", "each"] = "none"
    v2_no_band: bool = False


@dataclass(frozen=True)
class Player:
    player_id: str
    name: str
    team: str
    position: Literal["GK", "DF", "MF", "FW"]


@dataclass(frozen=True)
class Window:
    start_t: float  # events with t > start_t count
    end_t: float | None  # None = closes on half_end (half) or full_end (match) event
    end_marker: Literal["half_end", "full_end"] | None
    half: int | None = None  # set: only events with this "half" count (the clock restarts at 2700)


@dataclass
class MatchState:
    match_id: str
    t: float
    half: int
    home: str
    away: str
    team_names: dict[str, str]
    score: dict[str, int]  # keyed by team code
    possession_team: str | None
    events: list[dict]  # all events so far, contract MatchEvent shape
    open_template_ids: list[str]
    last_template_id: str | None
    open_window_count: int
    roster: dict[str, Player]


@dataclass(frozen=True)
class Candidate:
    template_id: str
    kind: Kind
    category: str
    params: dict
    text: str
    options: list[str]
    p: dict[str, float]
    window: Window
    resolve_by: float | Literal["HT", "FT"]
    player_id: str | None
    context: Ctx  # match context when asked (possession, trailing team); used to settle

    @property
    def key(self) -> str:
        """Identity used for repeat checks."""
        return f"{self.template_id}|{json.dumps(self.params, sort_keys=True)}|{self.player_id}"


@dataclass(frozen=True)
class SlotDecision:
    open_at: float
    kind: Kind
    shared: Candidate | None  # set for instant/window
    per_user: dict[str, Candidate] | None  # user_id -> candidate, set for player


@dataclass(frozen=True)
class StackDecision:
    """Questions asked together at a restart: kickoff or half time."""
    phase: Phase
    open_at: float  # match second the stack opens (the restart t)
    shared: list[Candidate]  # 0..2 window candidates, distinct template ids
    per_user: dict[str, Candidate] | None  # user_id -> candidate, None if no player card
