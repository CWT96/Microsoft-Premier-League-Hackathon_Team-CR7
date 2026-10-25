import json
from collections import Counter

import pytest

from backend.bank.loader import BANK_PATH, fill_text, load_bank, window_label
from backend.bank.types import MatchState, Player

LONG_TEAM = "T" * 30
LONG_PLAYER = "P" * 30


def _state() -> MatchState:
    return MatchState(
        match_id="m", t=0.0, half=1, home="AAA", away="BBB",
        team_names={"AAA": LONG_TEAM, "BBB": "U" * 30}, score={"AAA": 0, "BBB": 0},
        possession_team="AAA", events=[], open_template_ids=[], last_template_id=None,
        open_window_count=0, roster={},
    )


def test_bank_has_28_templates_by_kind():
    bank = load_bank()
    kinds = Counter(t.kind for t in bank.values())
    assert len(bank) == 28 and kinds == {"instant": 8, "window": 13, "player": 7}


def test_every_template_has_resolver_and_pricer():
    from backend.model.poisson import PRICERS
    from backend.resolve.primitives import PRIMITIVES
    for t in load_bank().values():
        assert t.resolve["primitive"] in PRIMITIVES and t.prob in PRICERS


def test_duplicate_id_raises(tmp_path):
    entry = json.loads(BANK_PATH.read_text())[8]
    assert entry["id"] == "goal_window"
    path = tmp_path / "dup.json"
    path.write_text(json.dumps([entry, entry]))
    with pytest.raises(ValueError, match="goal_window"):
        load_bank(path)


def test_invalid_entry_raises(tmp_path):
    entry = json.loads(BANK_PATH.read_text())[0]
    entry["options"] = ["only_one"]
    path = tmp_path / "bad.json"
    path.write_text(json.dumps([entry]))
    with pytest.raises(ValueError):
        load_bank(path)


def test_window_labels():
    assert window_label("5m") == "the next 5 minutes"
    assert window_label("half") == "the rest of this half"
    assert window_label("match") == "this match"
    assert window_label(60) == "the next minute"
    assert window_label(120) == "the next 2 minutes"
    assert window_label(90) == "the next 90 seconds"


def test_filled_text_fits_140_with_long_names():
    state = _state()
    player = Player("p1", LONG_PLAYER, "AAA", "MF")
    for t in load_bank().values():
        params = {}
        for k, v in t.params.items():
            if k == "line":
                params[k] = 10
            elif v == "tuned":
                params[k] = 600
            else:
                params[k] = max(v, key=lambda x: len(window_label(x)) if k.startswith("window") else 0)
        params["team"] = "AAA"
        params["stat"] = "completed passes"
        text = fill_text(t, params, state, player)
        assert len(text) <= 140, (t.id, len(text))
        assert "{" not in text, t.id


def test_fill_text_renders_names():
    t = load_bank()["who_shoots_first"]
    text = fill_text(t, {"window": "5m"}, _state(), None)
    assert LONG_TEAM in text and "the next 5 minutes" in text
