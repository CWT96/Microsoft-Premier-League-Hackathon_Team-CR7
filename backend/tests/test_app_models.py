"""Pydantic mirrors produce contract-valid payloads."""

from backend.app.models import (Axis, ByCategory, CategoryScore, CrowdSplit, FinalScore, MatchState,
                                Nudge, Option, Question, QuestionResult, Radar, Rank, Score, You, dump)
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
                   hit_rate=0.67, by_category=ByCategory(instant=cat, forecast=cat, player=cat),
                   best_streak=2, hints_used=0, tier="Super Fan",
                   radar=Radar(galaxy_brain=Axis(score=80, answered=3, correct=2)),
                   rank=Rank(room=1, global_=None))
    validate("score", dump(s))


def test_minimal_final_score_validates_with_defaults() -> None:
    cat = CategoryScore(issued=0, correct=0, points=0)
    s = FinalScore(user_id="u", match_id="m", final_score=0, issued=0, answered=0, correct=0,
                   hit_rate=0, by_category=ByCategory(instant=cat, forecast=cat, player=cat),
                   best_streak=0, hints_used=0, tier="Rookie")
    validate("score", dump(s))


def test_result_with_you_validates() -> None:
    r = QuestionResult(qid="q-1", status="void", correct_option=None, message="Plot twist!",
                       evidence=["e1"], crowd_split=CrowdSplit(global_={"forward": 40, "backward": 60}),
                       you=You(answered=True, option="forward"))
    payload = dump(r)
    assert payload["you"]["used_hint"] is False and payload["you"]["streak"] == 0
    validate("result", payload)


def test_match_state_and_nudge_validate() -> None:
    st = MatchState(t=10, half=1, home="NBR", away="CAL", score=Score(home=0, away=0),
                    mode="game", speed=1, cost=0.2)
    validate("state", dump(st))
    validate("nudge", dump(Nudge(text="Still with us?")))
