"""Catch-up card: code picks events and computes facts; text is LLM or template, guarded."""

import json
import math

import pytest

from backend.agents import llm
from backend.agents.catchup import build_catchup as _build
from backend.core.types import MatchEvent, Player, Roster


def _roster() -> Roster:
    players = (
        Player("nbr-8", "Mensah", "NBR", "MF", 8), Player("nbr-9", "Okoro", "NBR", "FW", 9),
        Player("nbr-4", "Diaz", "NBR", "DF", 4), Player("cal-1", "Ward", "CAL", "GK", 1),
    )
    return Roster("NBR", "CAL", "Northbridge", "Caldera", players)


def _pass(i: int, t: float, x0: float, x1: float, pid: str = "nbr-8") -> MatchEvent:
    return MatchEvent(id=f"e{i}", t=t, half=1, team="NBR", type="pass", player_id=pid,
                      start=(x0, 30.0), end=(x1, 30.0), outcome="complete")


def _attack() -> list[MatchEvent]:
    return [
        _pass(1, 100, 30, 36), _pass(2, 104, 36, 60, "nbr-4"), _pass(3, 108, 60, 63),
        _pass(4, 112, 63, 80), _pass(5, 116, 80, 82, "nbr-9"), _pass(6, 119, 82, 84),
        MatchEvent(id="e7", t=122, half=1, team="NBR", type="shot", player_id="nbr-9",
                   start=(87.0, 34.0), end=(105.0, 36.0), outcome="saved"),
        MatchEvent(id="e8", t=123, half=1, team="CAL", type="save", player_id="cal-1",
                   start=(0.0, 34.0)),
    ]


def build_catchup(ev, roster, card_id):
    return _build(ev, roster, card_id, "m1")


@pytest.fixture(autouse=True)
def _reset_client():
    yield
    llm.set_client(None)


def _cache_only(tmp_path):
    llm.set_client(llm.LLMClient(mode="cache", cache_dir=tmp_path))


def test_card_shape_and_window(tmp_path) -> None:
    _cache_only(tmp_path)
    card = build_catchup(_attack(), _roster(), "c-1")
    assert set(card) == {"card_id", "t_start", "t_end", "event_ids", "headline", "explanation",
                         "facts", "source"}
    assert card["source"] == "template"
    assert card["card_id"] == "c-1"
    assert card["t_start"] == 100 and card["t_end"] == 123
    assert card["headline"] and card["explanation"]


def test_picks_three_to_five_events_in_order_incl_final(tmp_path) -> None:
    _cache_only(tmp_path)
    ids = build_catchup(_attack(), _roster(), "c-1")["event_ids"]
    assert 3 <= len(ids) <= 5
    assert ids == sorted(ids, key=lambda i: int(i[1:]))  # chronological
    assert "e8" in ids  # the final event
    assert "e7" in ids  # the shot
    assert "e2" in ids and "e4" in ids  # the two biggest gains (24m, 17m)


def test_short_attack_keeps_everything(tmp_path) -> None:
    _cache_only(tmp_path)
    ev = _attack()[5:]
    assert build_catchup(ev, _roster(), "c-2")["event_ids"] == ["e6", "e7", "e8"]


def test_facts_are_computed_by_code(tmp_path) -> None:
    _cache_only(tmp_path)
    facts = build_catchup(_attack(), _roster(), "c-1")["facts"]
    assert facts["shot_dist_m"] == round(math.hypot(105 - 87, 34 - 34))
    assert facts["passes"] == 6
    assert facts["duration_s"] == 23
    assert facts["shooter"] == "Okoro"
    assert facts["saver"] == "Ward"
    assert facts["team_name"] == "Northbridge"


def test_attack_without_shot_has_no_shot_facts(tmp_path) -> None:
    _cache_only(tmp_path)
    ev = _attack()[:4] + [MatchEvent(id="e9", t=114, half=1, team="CAL", type="interception",
                                     player_id="cal-1", start=(66.0, 30.0))]
    card = build_catchup(ev, _roster(), "c-3")
    assert "shot_dist_m" not in card["facts"] and "shooter" not in card["facts"]
    assert card["event_ids"][-1] == "e9"
    assert card["explanation"]


def test_template_text_only_uses_fact_numbers(tmp_path) -> None:
    _cache_only(tmp_path)
    card = build_catchup(_attack(), _roster(), "c-1")
    assert llm.number_guard(card["headline"] + " " + card["explanation"], card["facts"])
    assert "Okoro" in card["explanation"] and "18" in card["explanation"]


def test_llm_text_used_when_guarded_ok(tmp_path) -> None:
    reply = json.dumps({"headline": "Northbridge pushed forward",
                        "explanation": "Okoro shot from 18 metres and Ward saved it."})
    llm.set_client(llm.LLMClient(mode="fake", cache_dir=tmp_path, fake=lambda a, p: reply))
    card = build_catchup(_attack(), _roster(), "c-1")
    assert card["headline"] == "Northbridge pushed forward" and card["source"] == "llm"


def test_llm_text_with_bad_number_falls_back(tmp_path) -> None:
    reply = json.dumps({"headline": "Big chance", "explanation": "Okoro shot from 40 metres."})
    llm.set_client(llm.LLMClient(mode="fake", cache_dir=tmp_path, fake=lambda a, p: reply))
    card = build_catchup(_attack(), _roster(), "c-1")
    assert "40" not in card["explanation"]
    assert card["headline"] != "Big chance"


def test_deterministic(tmp_path) -> None:
    _cache_only(tmp_path)
    assert build_catchup(_attack(), _roster(), "c-1") == build_catchup(_attack(), _roster(), "c-1")


def test_empty_attack_rejected() -> None:
    with pytest.raises(ValueError):
        build_catchup([], _roster(), "c-0")


def test_unknown_player_id_does_not_crash(tmp_path) -> None:
    _cache_only(tmp_path)
    ev = [_pass(1, 10, 30, 50, "ghost"), _pass(2, 14, 50, 70, None)]
    card = build_catchup(ev, _roster(), "c-4")
    assert card["event_ids"] == ["e1", "e2"]


def test_unsafe_llm_text_falls_back(tmp_path) -> None:
    reply = json.dumps({"headline": "Big chance", "explanation": "Read more at http://x.io"})
    llm.set_client(llm.LLMClient(mode="fake", cache_dir=tmp_path, fake=lambda a, p: reply))
    card = build_catchup(_attack(), _roster(), "c-1")
    assert card["source"] == "template" and "http" not in card["explanation"]


def test_match_id_is_required() -> None:
    with pytest.raises(TypeError):
        _build(_attack(), _roster(), "c-1")
