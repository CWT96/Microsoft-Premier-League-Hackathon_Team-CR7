"""Pydantic mirrors of ``contracts/*.schema.json`` for WebSocket and REST payloads.

Do not invent fields: every field here exists in the matching schema. Dump with
``model_dump(exclude_none=False)`` and validate in tests with ``backend/tests/contract_utils``.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


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
    crowd_split: dict[str, dict[str, float]]
    you: You | None = None


class CategoryScore(_Strict):
    issued: int = Field(ge=0)
    correct: int = Field(ge=0)
    points: int = Field(ge=0)


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
    by_category: dict[Literal["instant", "forecast", "player"], CategoryScore]
    best_streak: int = Field(ge=0)
    hints_used: int = Field(ge=0)
    terms_learned: list[str] = Field(default_factory=list)
    tier: Literal["Rookie", "Rising Star", "Super Fan"] | None = None
    radar: Radar | None = None
    rank: Rank | None = None
    summary: str | None = None


class Envelope(_Strict):
    """Every WebSocket message: ``{"type": ..., "data": {...}}``."""

    type: str
    data: dict[str, Any]


def dump(model: BaseModel) -> dict[str, Any]:
    """Contract-shaped dict (aliases applied, unset optional fields dropped)."""
    return model.model_dump(by_alias=True, exclude_unset=True)
