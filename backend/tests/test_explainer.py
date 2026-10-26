"""Explainer [5]: LLM text only when it passes the guard, otherwise a template."""

import pytest

from backend.agents import llm
from backend.agents.explainer import explain as _explain
from backend.app.models import Option, Question
from backend.core.types import Outcome


def _question(template_id: str = "next_pass_dir", **kw) -> Question:
    base = dict(qid="q-1", category="instant", template_id=template_id,
                text="Next pass: forward or back?",
                options=[Option(id="forward", label="Forward"),
                         Option(id="backward", label="Back")],
                open_at=100.0, lock_at=106.0, resolve_by=121.0, hint_available=True)
    base.update(kw)
    return Question(**base)


def _outcome(status: str = "resolved", correct: str | None = "forward", **facts) -> Outcome:
    return Outcome(qid="q-1", status=status, correct_option=correct, evidence=("e1",),
                   resolved_at=110.0, facts=facts)


def explain(outcome, question, names):
    return _explain(outcome, question, names, "m1")


@pytest.fixture(autouse=True)
def _reset_client():
    yield
    llm.set_client(None)


def _use_fake(tmp_path, reply):
    calls = []

    def fake(agent: str, prompt: str):
        calls.append((agent, prompt))
        return reply

    llm.set_client(llm.LLMClient(mode="fake", cache_dir=tmp_path, fake=fake))
    return calls


def _use_cache_only(tmp_path):
    llm.set_client(llm.LLMClient(mode="cache", cache_dir=tmp_path))


def test_cache_miss_uses_template_with_required_keys(tmp_path) -> None:
    _use_cache_only(tmp_path)
    out = explain(_outcome(dx_m=16), _question(), {})
    assert set(out) == {"message", "explanation", "new_term", "source"}
    assert out["source"] == "template"
    assert "Forward" in out["message"]
    assert "16" in out["explanation"]
    assert out["new_term"]


def test_template_numbers_come_only_from_facts(tmp_path) -> None:
    _use_cache_only(tmp_path)
    out = explain(_outcome(), _question(), {})
    assert not any(ch.isdigit() for ch in out["explanation"] + out["message"])


def test_llm_text_used_when_guard_passes(tmp_path) -> None:
    reply = '{"message": "Forward it was!", "explanation": "The pass travelled 16 metres '
    reply += 'toward goal.", "new_term": "forward pass"}'
    calls = _use_fake(tmp_path, reply)
    out = explain(_outcome(dx_m=16), _question(), {})
    assert out == {"message": "Forward it was!",
                   "explanation": "The pass travelled 16 metres toward goal.",
                   "new_term": "forward pass", "source": "llm"}
    assert calls and calls[0][0] == "explainer"
    assert '"dx_m": 16' in calls[0][1]


def test_llm_text_with_invented_number_falls_back_to_template(tmp_path) -> None:
    _use_fake(tmp_path, '{"message": "Forward!", "explanation": "It went 99 metres.", '
                        '"new_term": null}')
    out = explain(_outcome(dx_m=16), _question(), {})
    assert "99" not in out["explanation"]
    assert "16" in out["explanation"]


def test_llm_text_may_use_question_params(tmp_path) -> None:
    q = _question("goals_over", text="3 or more goals in total?", params={"line": 3},
                  options=[Option(id="yes", label="Yes"), Option(id="no", label="No")],
                  resolve_by="FT")
    _use_fake(tmp_path, '{"message": "Yes!", "explanation": "That is 3 or more goals.", '
                        '"new_term": null}')
    out = explain(_outcome(correct="yes"), q, {})
    assert out["explanation"] == "That is 3 or more goals."


def test_llm_mentioning_void_is_rejected(tmp_path) -> None:
    _use_fake(tmp_path, '{"message": "Void!", "explanation": "Nothing.", "new_term": null}')
    out = explain(_outcome(), _question(), {})
    assert "void" not in (out["message"] + out["explanation"]).lower()


@pytest.mark.parametrize("reply", ["garbage", '{"message": "x"}', '{"message": 1}', None])
def test_bad_llm_reply_falls_back(tmp_path, reply) -> None:
    _use_fake(tmp_path, reply or "")
    out = explain(_outcome(), _question(), {})
    assert out["message"] and out["explanation"]


def test_overlong_llm_text_is_rejected(tmp_path) -> None:
    _use_fake(tmp_path, '{"message": "' + "a" * 200 + '", "explanation": "ok", "new_term": null}')
    assert len(explain(_outcome(), _question(), {})["message"]) < 100


def test_void_outcome_is_playful_and_never_says_void(tmp_path) -> None:
    calls = _use_fake(tmp_path, "{}")
    seen = set()
    for i in range(12):
        o = Outcome(qid=f"q-{i}", status="void", correct_option=None, evidence=())
        out = explain(o, _question(qid=f"q-{i}"), {})
        text = " ".join(str(v) for v in out.values() if v)
        assert "void" not in text.lower()
        assert out["new_term"] is None
        seen.add(out["message"])
    assert len(seen) > 1
    assert not calls  # void lines are templates, no model call


def test_void_is_deterministic(tmp_path) -> None:
    _use_cache_only(tmp_path)
    o = Outcome(qid="q-7", status="void", correct_option=None, evidence=())
    assert explain(o, _question(qid="q-7"), {}) == explain(o, _question(qid="q-7"), {})


def test_every_catalog_template_has_a_template_text(tmp_path) -> None:
    _use_cache_only(tmp_path)
    yn = [Option(id="yes", label="Yes"), Option(id="no", label="No")]
    for tid in ("keep_ball", "reach_final_third", "shot_soon", "goals_over", "team_wins",
                "card_second_half", "second_half_goal", "player_stat_over",
                "player_touch_window", "unknown_new_template"):
        q = _question(tid, options=yn, params={"stat": "saves"}, player_id="nbr-1")
        for correct in ("yes", "no"):
            out = explain(_outcome(correct=correct), q, {"nbr-1": "Ward"})
            assert out["message"] and out["explanation"], tid


def test_player_name_used_when_known(tmp_path) -> None:
    _use_cache_only(tmp_path)
    yn = [Option(id="yes", label="Yes"), Option(id="no", label="No")]
    q = _question("player_touch_window", options=yn, player_id="nbr-9", category="player")
    out = explain(_outcome(correct="yes"), q, {"nbr-9": "Mensah"})
    assert "Mensah" in out["explanation"]


@pytest.mark.parametrize("bad", ["See https://x.io now", "<b>hi</b>", "has `ticks`",
                                 "two\\nlines", "Seven passes"])
def test_unsafe_llm_text_falls_back_and_is_counted(tmp_path, bad) -> None:
    import json
    llm.stats.clear()
    _use_fake(tmp_path, json.dumps({"message": "Nice", "explanation": bad, "new_term": None}))
    out = explain(_outcome(dx_m=16), _question(), {})
    assert out["source"] == "template" and out["explanation"] != bad
    assert sum(v for (a, o), v in llm.stats.items() if o in ("unsafe", "guard")) == 1


def test_match_id_is_required() -> None:
    with pytest.raises(TypeError):
        _explain(_outcome(), _question(), {})
