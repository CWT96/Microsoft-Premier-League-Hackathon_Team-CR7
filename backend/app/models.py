"""Pydantic mirrors of ``contracts/*.schema.json`` for WebSocket and REST payloads.

Do not invent fields: every field here exists in the matching schema. Dump with ``dump()``
(every field, nulls included; non-nullable fields therefore never default to None) and
validate in tests with ``backend/tests/contract_utils``. ``timing.decision`` and
``match.tracking`` are built by ``TimingDecision.to_contract`` / ``TrackingFrame.to_contract``
in ``backend/core/types.py``.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_serializer


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Option(_Strict):
    id: str
    label: str = Field(max_length=24)


class Question(_Strict):
    """``question.open`` payload (``question.schema.json``). Every question is binary."""

    qid: str
    category: Literal["instant", "forecast", "player"]
    template_id: str
    text: str = Field(max_length=140)
    options: list[Option] = Field(min_length=2, max_length=2)
    open_at: float
    lock_at: float
    resolve_by: float | Literal["HT", "FT"]
    hint_available: bool
    player_id: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    tone: str | None = Field(default=None, max_length=40)
    situation: str | None = None
    lock_rule: Literal["fixed", "on_restart"] = "fixed"


class You(_Strict):
    answered: bool
    option: str | None = None
    correct: bool | None = None
    used_hint: bool = False
    streak: int = Field(default=0, ge=0)


class CrowdSplit(_Strict):
    """Share (0–100) choosing each option id, per social scope."""

    global_: dict[str, float] | None = Field(default=None, alias="global")
    room: dict[str, float] | None = None
    friends: dict[str, float] | None = None

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    @model_serializer(mode="wrap")
    def _drop_missing_scopes(self, handler: Any) -> dict[str, Any]:
        # A scope with no data is left out: the schema does not allow null here.
        return {k: v for k, v in handler(self).items() if v is not None}


class QuestionResult(_Strict):
    """``question.result`` payload (``result.schema.json``), sent per user."""

    qid: str
    status: Literal["resolved", "void"]
    correct_option: str | None
    message: str
    explanation: str | None = None
    new_term: str | None = None
    evidence: list[str]
    facts: dict[str, Any] = Field(default_factory=dict)
    crowd_split: CrowdSplit
    you: You | None = None


class CategoryScore(_Strict):
    issued: int = Field(ge=0)
    correct: int = Field(ge=0)
    points: int = Field(ge=0)


class ByCategory(_Strict):
    instant: CategoryScore
    forecast: CategoryScore
    player: CategoryScore


class Axis(_Strict):
    score: int = Field(ge=0, le=100)
    answered: int = Field(ge=0)
    correct: int = Field(ge=0)


class Radar(_Strict):
    crystal_ball: Axis | None = None
    galaxy_brain: Axis | None = None
    pause_pro: Axis | None = None
    bestie_mode: Axis | None = None
    lightning_thumbs: Axis | None = None
    on_fire: Axis | None = None


class Rank(_Strict):
    room: int | None = None
    global_: int | None = Field(default=None, alias="global")
    friends: int | None = None

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class FinalScore(_Strict):
    """``score.final`` payload (``score.schema.json``)."""

    user_id: str
    match_id: str
    final_score: int = Field(ge=0, le=1000)
    issued: int = Field(ge=0)
    answered: int = Field(ge=0)
    correct: int = Field(ge=0)
    hit_rate: float = Field(ge=0, le=1)
    by_category: ByCategory
    best_streak: int = Field(ge=0)
    hints_used: int = Field(ge=0)
    terms_learned: list[str] = Field(default_factory=list)
    tier: Literal["Rookie", "Rising Star", "Super Fan"]
    radar: Radar = Field(default_factory=Radar)
    rank: Rank = Field(default_factory=Rank)
    summary: str | None = None


class Score(_Strict):
    home: int = Field(ge=0)
    away: int = Field(ge=0)


class MatchState(_Strict):
    """``match.state`` payload (``state.schema.json``), per user, once per match second."""

    t: float = Field(ge=0)
    half: Literal[1, 2]
    home: str
    away: str
    score: Score
    mode: Literal["lobby", "live", "game", "attack", "break", "full_time"]
    break_kind: Literal["half_time", "water_break"] | None = None
    speed: float = Field(ge=1, le=15)
    dull_score: float | None = Field(default=None, ge=0, le=1)
    cost: float | None = Field(default=None, ge=0, le=1)
    movement: float | None = Field(default=None, ge=0, le=1)
    pressure_m: float | None = Field(default=None, ge=0)
    situation: str | None = None


class Nudge(_Strict):
    """``nudge`` payload: the user's feed paused after 2 unanswered cards."""

    text: str = Field(max_length=80)


class Envelope(_Strict):
    """Every WebSocket message: ``{"type": ..., "data": {...}}``."""

    type: str
    data: dict[str, Any]


def dump(model: BaseModel) -> dict[str, Any]:
    """Contract-shaped dict: aliases applied, every field present, nulls kept."""
    return model.model_dump(by_alias=True, mode="json")
