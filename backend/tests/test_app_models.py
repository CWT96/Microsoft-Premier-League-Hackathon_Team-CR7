"""Pydantic mirrors produce contract-valid payloads."""

from backend.app.models import Axis, CategoryScore, FinalScore, Option, Question, Radar, Rank, dump
from backend.tests.contract_utils import validate


def test_question_validates() -> None:
    q = Question(qid="q-1", category="instant", template_id="next_pass_dir", text="Next pass?",
                 options=[Option(id="forward", label="Forward"), Option(id="backward", label="Back")],
                 open_at=10, lock_at=16, resolve_by=31, hint_available=True,
                 situation="calm_play", lock_rule="fixed")
    validate("question", dump(q))


def test_final_score_with_radar_validates() -> None:
    cat = CategoryScore(issued=1, correct=1, points=10)
    s = FinalScore(user_id="u", match_id="m", final_score=700, issued=3, answered=3, correct=2,
                   hit_rate=0.67, by_category={"instant": cat, "forecast": cat, "player": cat},
                   best_streak=2, hints_used=0, tier="Super Fan",
                   radar=Radar(galaxy_brain=Axis(score=80, answered=3, correct=2)),
                   rank=Rank(room=1, global_=None))
    validate("score", dump(s))
