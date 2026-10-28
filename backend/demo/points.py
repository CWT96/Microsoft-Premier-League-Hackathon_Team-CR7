"""Points for one fan call in the demo: answered, correct, and streak bonus."""

from __future__ import annotations

from dataclasses import dataclass

ANSWERED_POINTS = 10
CORRECT_POINTS = 100
STREAK_STEP_POINTS = 50
STREAK_BONUS_FROM = 3


@dataclass(frozen=True)
class CallScore:
    """Outcome of one call; `streak` is the streak after this call."""

    too_quick: bool
    correct: bool
    base: int
    correct_bonus: int
    streak_bonus: int
    points: int
    streak: int


def score_call(pick: str | None, answer: str, streak: int) -> CallScore:
    """Score a call. `pick` is None when unanswered; `streak` is the streak before it."""
    if pick is None:
        return CallScore(True, False, 0, 0, 0, 0, streak)
    base = ANSWERED_POINTS
    if pick != answer:
        return CallScore(False, False, base, 0, 0, base, 0)
    new_streak = streak + 1
    streak_bonus = 0
    if new_streak >= STREAK_BONUS_FROM:
        streak_bonus = STREAK_STEP_POINTS * (new_streak - 2)
    points = base + CORRECT_POINTS + streak_bonus
    return CallScore(False, True, base, CORRECT_POINTS, streak_bonus, points, new_streak)
