"""Scoring summary [6b]: praise only, numbers from the user's real card."""

import json

import pytest

from backend.agents import llm
from backend.agents.scoring_summary import summarize as _summarize
from backend.app.models import (ByCategory, CategoryScore, FinalScore)


def _score(**kw) -> FinalScore:
    base = dict(
        user_id="u1", match_id="m1", final_score=640, issued=12, answered=10, correct=7,
        hit_rate=0.7,
        by_category=ByCategory(instant=CategoryScore(issued=6, correct=5, points=330),
                               forecast=CategoryScore(issued=3, correct=1, points=90),
                               player=CategoryScore(issued=3, correct=1, points=220)),
        best_streak=4, hints_used=2, terms_learned=["possession", "final third"],
        tier="Rising Star")
    base.update(kw)
    return FinalScore(**base)


def summarize(fs, facts):
    return _summarize(fs, facts)[0]


@pytest.fixture(autouse=True)
def _reset_client():
    yield
    llm.set_client(None)


def _cache_only(tmp_path):
    llm.set_client(llm.LLMClient(mode="cache", cache_dir=tmp_path))


def test_template_is_positive_and_uses_real_numbers(tmp_path) -> None:
    _cache_only(tmp_path)
    text = summarize(_score(), {})
    assert "5 of 6" in text  # best category: quick calls
    assert "4" in text  # best streak
    low = text.lower()
    for bad in ("wrong", "miss", "fail", "unfortunately", "only", "but ", "not"):
        assert bad not in low


def test_template_numbers_all_in_card(tmp_path) -> None:
    _cache_only(tmp_path)
    fs = _score()
    text = summarize(fs, {})
    allowed = fs.model_dump()
    assert llm.number_guard(text, allowed)


def test_zero_answers_still_positive(tmp_path) -> None:
    _cache_only(tmp_path)
    fs = _score(final_score=0, answered=0, correct=0, hit_rate=0.0, best_streak=0, hints_used=0,
                terms_learned=[],
                by_category=ByCategory(instant=CategoryScore(issued=6, correct=0, points=0),
                                       forecast=CategoryScore(issued=3, correct=0, points=0),
                                       player=CategoryScore(issued=3, correct=0, points=0)))
    text = summarize(fs, {})
    assert text
    assert "0 of" not in text
    assert "wrong" not in text.lower()


def test_llm_summary_used_when_guarded_ok(tmp_path) -> None:
    reply = json.dumps({"summary": "You called 7 questions right. Nice work on the quick calls."})
    llm.set_client(llm.LLMClient(mode="fake", cache_dir=tmp_path, fake=lambda a, p: reply))
    assert summarize(_score(), {}).startswith("You called 7 questions right")


def test_llm_summary_with_invented_number_falls_back(tmp_path) -> None:
    reply = json.dumps({"summary": "You got 9 right. Great."})
    llm.set_client(llm.LLMClient(mode="fake", cache_dir=tmp_path, fake=lambda a, p: reply))
    assert "9 right" not in summarize(_score(), {})


def test_llm_summary_with_negative_wording_falls_back(tmp_path) -> None:
    reply = json.dumps({"summary": "You got 7 right but missed some."})
    llm.set_client(llm.LLMClient(mode="fake", cache_dir=tmp_path, fake=lambda a, p: reply))
    assert "missed" not in summarize(_score(), {})


def test_extra_facts_are_allowed_numbers(tmp_path) -> None:
    reply = json.dumps({"summary": "You beat 3 friends in your room."})
    llm.set_client(llm.LLMClient(mode="fake", cache_dir=tmp_path, fake=lambda a, p: reply))
    assert summarize(_score(), {"friends_beaten": 3}) == "You beat 3 friends in your room."


def test_deterministic(tmp_path) -> None:
    _cache_only(tmp_path)
    assert summarize(_score(), {}) == summarize(_score(), {})


def test_source_is_reported(tmp_path) -> None:
    _cache_only(tmp_path)
    assert _summarize(_score(), {})[1] == "template"
    reply = json.dumps({"summary": "You called 7 questions right."})
    llm.set_client(llm.LLMClient(mode="fake", cache_dir=tmp_path, fake=lambda a, p: reply))
    assert _summarize(_score(), {}) == ("You called 7 questions right.", "llm")


@pytest.mark.parametrize("word", ["loss", "didn't", "not", "worse", "Wrong"])
def test_new_negative_words_rejected(tmp_path, word) -> None:
    reply = json.dumps({"summary": f"You got 7 right, {word} at times."})
    llm.set_client(llm.LLMClient(mode="fake", cache_dir=tmp_path, fake=lambda a, p: reply))
    assert _summarize(_score(), {})[1] == "template"


def test_negative_match_is_word_bounded(tmp_path) -> None:
    reply = json.dumps({"summary": "You got 7 right and kept a notable pace."})
    llm.set_client(llm.LLMClient(mode="fake", cache_dir=tmp_path, fake=lambda a, p: reply))
    assert _summarize(_score(), {})[1] == "llm"


def test_unsafe_text_rejected(tmp_path) -> None:
    reply = json.dumps({"summary": "Great, see https://x.io"})
    llm.set_client(llm.LLMClient(mode="fake", cache_dir=tmp_path, fake=lambda a, p: reply))
    assert _summarize(_score(), {})[1] == "template"
