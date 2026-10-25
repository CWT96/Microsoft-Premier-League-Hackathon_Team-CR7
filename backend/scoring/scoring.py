"""scoring.py — positive scoring formula, fun radar and rank rules (pure functions, no LLM).

Last Modified: 2026-10-24 23:00, review round 1 fixes.
Inputs:  none (callers pass Answer and Outcome objects)
Outputs: FinalScore model (backend/app/models.py), best-moment dict

Workflow:
  - Every answered non-void card earns points for taking part, plus a bonus when correct.
  - final = 600 * quality (points per answer vs typical rate) + 400 * sqrt(participation).
  - Category points split `final` by largest remainder so they sum exactly.
  - Ranks appear only in the top half of a scope; the radar never drops below a floor.

CAUTION: Radar axes read moment situation, category, latency and streak only, never template ids.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Mapping, Sequence

from backend.app.models import Axis, ByCategory, CategoryScore, FinalScore, Radar, Rank
from backend.core.types import Answer, Outcome

# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
#
# Constants
#
# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

PARTICIPATION_POINTS = 10
CORRECT_POINTS = 20
HINT_FACTOR = 0.5
STREAK_MULTIPLIERS = (1.0, 1.2, 1.5)  # 1st, 2nd, 3rd+ correct in a row
FINAL_MAX = 1000
QUALITY_WEIGHT = 600
PARTICIPATION_WEIGHT = 400
TIER_RISING_STAR_MIN = 400
TIER_SUPER_FAN_MIN = 700
TYPICAL_POINTS_PER_CARD = 22.8  # one C C W C W cycle (30+34+10+30+10) / 5, no hints
WEIGHT_SCALE = 10  # points are scaled to integers before the largest-remainder split

CATEGORIES = ("instant", "forecast", "player")
CALM_SITUATIONS = frozenset({"calm_play", "stalled_near_box"})
PAUSE_SITUATIONS = frozenset({
    "throw_in", "goal_kick", "set_piece_wait", "referee_decision", "substitution",
    "injury", "water_break", "after_goal", "kickoff", "half_time",
})
RADAR_MAX = 100
RADAR_FLOOR = 35  # any axis with a played card scores at least this
LATENCY_FAST_S = 1.0  # at or under this -> 100
LATENCY_SLOW_S = 6.0  # at this -> 40
LATENCY_SLOW_SCORE = 40
LATENCY_SLOPE = (RADAR_MAX - LATENCY_SLOW_SCORE) / (LATENCY_SLOW_S - LATENCY_FAST_S)  # 12/s
FIRE_STREAK_FULL = 5  # streak at which On Fire reads 100
TOP_HALF = 0.5

BEST_STREAK_MIN = 3
PLAYER_CORRECT_MIN = 2
FORECAST_CORRECT_MIN = 1
SMALL_STREAK_MIN = 2


# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
#
# Points
#
# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%


def _scored(answers: Sequence[Answer], outcomes: Mapping[str, Outcome]) -> list[dict]:
    """Per-card record for every non-void shown card, in issue order.

    Keys: answer, answered, correct, points, streak (streak after this card).
    Unanswered cards carry 0 points and do not touch the streak.
    """
    ordered = sorted(answers, key=lambda a: (a.half, a.open_at, a.qid))
    rows: list[dict] = []
    streak = 0
    for a in ordered:
        o = outcomes.get(a.qid)
        if o is None or o.status == "void":
            continue
        answered = a.option is not None
        correct = answered and a.option == o.correct_option
        points = 0.0
        if answered:
            points = PARTICIPATION_POINTS
            if correct:
                mult = STREAK_MULTIPLIERS[min(streak, len(STREAK_MULTIPLIERS) - 1)]
                points += CORRECT_POINTS * mult * (HINT_FACTOR if a.used_hint else 1.0)
                streak += 1
            else:
                streak = 0
        rows.append({"answer": a, "answered": answered, "correct": correct,
                     "points": points, "streak": streak})
    return rows


def earned_points(answers: Sequence[Answer], outcomes: Mapping[str, Outcome]) -> float:
    """Total points earned by one user."""
    return sum(r["points"] for r in _scored(answers, outcomes))


def tier_for(final: int) -> str:
    """Tier name for a final score."""
    if final >= TIER_SUPER_FAN_MIN:
        return "Super Fan"
    if final >= TIER_RISING_STAR_MIN:
        return "Rising Star"
    return "Rookie"


def _largest_remainder(total: int, weights: Sequence[float]) -> list[int]:
    """Split integer ``total`` proportionally to ``weights``; parts sum to ``total``."""
    ints = [round(w * WEIGHT_SCALE) for w in weights]
    s = sum(ints)
    if s <= 0 or total <= 0:
        return [0] * len(ints)
    exact = [total * w / s for w in ints]
    parts = [math.floor(x) for x in exact]
    order = sorted(range(len(ints)), key=lambda i: (-(exact[i] - parts[i]), i))
    for i in order[: total - sum(parts)]:
        parts[i] += 1
    assert sum(parts) == total, "largest-remainder parts must sum to total"
    return parts


# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
#
# Ranks, radar, best moment
#
# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%


def _flattering_rank(final: int, others: Sequence[int]) -> int | None:
    """1-based rank if in the top half of the scope, else None.

    ``others`` excludes the user. Ties share the better rank (only strictly higher scores
    count against the user). Rounding elsewhere is Python's ``round`` (half to even).
    """
    if not others:
        return None
    rank = 1 + sum(1 for x in others if x > final)
    return rank if rank <= (len(others) + 1) * TOP_HALF else None


def _points_axis(rows: list[dict]) -> Axis | None:
    played = [r for r in rows if r["answered"]]
    if not played:
        return None
    rate = sum(r["points"] for r in played) / (len(played) * TYPICAL_POINTS_PER_CARD)
    score = max(RADAR_FLOOR, min(RADAR_MAX, round(RADAR_MAX * rate)))
    return Axis(score=score, answered=len(played), correct=sum(r["correct"] for r in played))


def _radar(rows: list[dict], best_streak: int) -> Radar:
    def pick(pred) -> Axis | None:
        return _points_axis([r for r in rows if pred(r["answer"])])

    played = [r for r in rows if r["answered"]]
    totals = (len(played), sum(r["correct"] for r in played))
    lightning = fire = None
    if played:
        fire = Axis(score=max(RADAR_FLOOR, min(RADAR_MAX, round(RADAR_MAX * best_streak
                                                               / FIRE_STREAK_FULL))),
                    answered=totals[0], correct=totals[1])
    lats = [r["answer"].answer_latency_s for r in played
            if r["answer"].answer_latency_s is not None]
    if lats:
        med = statistics.median(lats)
        score = RADAR_MAX - LATENCY_SLOPE * max(0.0, med - LATENCY_FAST_S)
        lightning = Axis(score=max(RADAR_FLOOR, min(RADAR_MAX, round(score))),
                         answered=totals[0], correct=totals[1])
    return Radar(
        crystal_ball=pick(lambda a: a.category == "forecast"),
        galaxy_brain=pick(lambda a: a.category == "instant" and a.situation in CALM_SITUATIONS),
        pause_pro=pick(lambda a: a.category == "instant" and a.situation in PAUSE_SITUATIONS),
        bestie_mode=pick(lambda a: a.category == "player"),
        lightning_thumbs=lightning,
        on_fire=fire,
    )


def _best_streak(rows: list[dict]) -> int:
    best = run = 0
    for r in rows:
        if not r["answered"]:
            continue
        run = run + 1 if r["correct"] else 0
        best = max(best, run)
    return best


def best_moment(answers: Sequence[Answer], outcomes: Mapping[str, Outcome]) -> dict:
    """Most flattering highlight for the UI when no rank is shown."""
    rows = _scored(answers, outcomes)
    played = [r for r in rows if r["answered"]]
    best = _best_streak(rows)
    player = sum(1 for r in played if r["correct"] and r["answer"].category == "player")
    forecast = sum(1 for r in played if r["correct"] and r["answer"].category == "forecast")
    correct = sum(1 for r in played if r["correct"])
    candidates = (
        ("best_streak", best, BEST_STREAK_MIN),
        ("player_correct", player, PLAYER_CORRECT_MIN),
        ("forecast_correct", forecast, FORECAST_CORRECT_MIN),
        ("best_streak", best, SMALL_STREAK_MIN),
        ("correct", correct, 1),
    )
    for kind, value, minimum in candidates:
        if value >= minimum:
            return {"kind": kind, "value": value}
    return {"kind": "answered", "value": len(played)}


# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
#
# Final score
#
# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%


def final_score(
    user_id: str,
    match_id: str,
    answers: Sequence[Answer],
    outcomes: Mapping[str, Outcome],
    n_typical: int,
    terms_learned: Sequence[str] = (),
    others_final: Sequence[int] = (),
    room_final: Sequence[int] = (),
    friends_final: Sequence[int] = (),
) -> FinalScore:
    """Build the score card payload for one user.

    Args:
        user_id: User id.
        match_id: Match id.
        answers: Every card shown to this user (unanswered ones have ``option=None``).
        outcomes: Shared outcomes by qid; void or missing outcomes are skipped.
        n_typical: Cards a typical engaged player answers in this match.
        terms_learned: Football terms to list on the card.
        others_final: Final scores of the other global users.
        room_final: Final scores of the other users in the room.
        friends_final: Final scores of the user's friends.

    Returns:
        FinalScore with ranks only where the user is in the top half.

    Raises:
        ValueError: If ``n_typical`` is not positive.
    """
    if n_typical <= 0:
        raise ValueError("n_typical must be positive")
    rows = _scored(answers, outcomes)
    earned = sum(r["points"] for r in rows)
    played = [r for r in rows if r["answered"]]
    final = 0
    if played:
        quality = min(1.0, earned / len(played) / TYPICAL_POINTS_PER_CARD)
        participation = math.sqrt(min(1.0, len(played) / n_typical))
        final = min(FINAL_MAX, round(QUALITY_WEIGHT * quality
                                     + PARTICIPATION_WEIGHT * participation))
    correct = sum(r["correct"] for r in played)
    best = _best_streak(rows)

    def cat_rows(c: str) -> list[dict]:
        return [r for r in rows if r["answer"].category == c]

    weights = [sum(r["points"] for r in cat_rows(c)) for c in CATEGORIES]
    shares = _largest_remainder(final, weights)
    by_cat = ByCategory(**{
        c: CategoryScore(issued=len(cat_rows(c)),
                         correct=sum(r["correct"] for r in cat_rows(c)), points=shares[i])
        for i, c in enumerate(CATEGORIES)
    })
    return FinalScore(
        user_id=user_id, match_id=match_id, final_score=final, issued=len(rows),
        answered=len(played), correct=correct,
        hit_rate=correct / len(played) if played else 0.0,
        by_category=by_cat, best_streak=best,
        hints_used=sum(1 for r in played if r["answer"].used_hint),
        terms_learned=list(terms_learned), tier=tier_for(final),
        radar=_radar(rows, best),
        rank=Rank(room=_flattering_rank(final, room_final),
                  global_=_flattering_rank(final, others_final),
                  friends=_flattering_rank(final, friends_final)),
    )
