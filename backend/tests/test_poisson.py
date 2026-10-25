import pytest

from backend.bank.loader import load_bank
from backend.bank.types import MatchState, Player
from backend.model.poisson import (PRICERS, p_at_least, p_final, p_first, p_more,
                                   tune_player)
from backend.model.priors import INSTANT_DEFAULTS, POSITION_RATES_90


def _state(t=600.0, half=1, score=None, events=None) -> MatchState:
    return MatchState(
        match_id="m", t=t, half=half, home="H", away="A", team_names={"H": "H", "A": "A"},
        score=score or {"H": 0, "A": 0}, possession_team="H", events=events or [],
        open_template_ids=[], last_template_id=None, open_window_count=0, roster={},
    )


def test_goal_10_minutes_is_26_percent():
    assert p_at_least(2.7, 10, 1) == pytest.approx(0.259, abs=0.002)


def test_penalty_rest_of_match_from_10min():
    assert p_at_least(0.3, 80, 1) == pytest.approx(0.234, abs=0.002)


def test_at_least_zero_is_certain():
    assert p_at_least(2.7, 10, 0) == 1.0


def test_first_of_sums_to_one():
    r = p_first(10, 15, 5)
    assert set(r) == {"a", "b", "none"}
    assert sum(r.values()) == pytest.approx(1.0)
    assert r["b"] > r["a"]


def test_p_more_symmetric_below_half():
    assert p_more(10, 10, 10) < 0.5
    assert p_more(20, 5, 10) == pytest.approx(0.756, abs=0.005)


def test_kickoff_team_win_about_37_percent():
    assert p_final("team_wins", {"H": 0, "A": 0}, "H", "A", 90) == pytest.approx(0.37, abs=0.02)


def test_three_nil_win_is_near_certain():
    assert p_final("team_wins", {"H": 3, "A": 0}, "H", "A", 20) > 0.95


def test_both_score_and_equalise():
    assert p_final("both_score", {"H": 1, "A": 1}, None, None, 10) == pytest.approx(1.0)
    assert 0 < p_final("both_score", {"H": 0, "A": 0}, None, None, 90) < 0.7
    assert p_final("equalise", {"H": 0, "A": 1}, "H", "A", 10) == pytest.approx(0.139, abs=0.002)


def test_gk_touch_window_tunes_near_half():
    w, p = tune_player("GK", "touch", "window_s", 45)
    assert w in (90, 120) and 0.4 <= p <= 0.6


def test_slow_stats_use_long_windows():
    w, p = tune_player("FW", "shot", "window_s", 35)
    assert w == 1500 and 0.4 <= p <= 0.6


def test_window_capped_by_minutes_left():
    w, _ = tune_player("FW", "shot", "window_s", 5)
    assert w <= 300


def test_line_tuning():
    line, p = tune_player("MF", "pass_complete", "line", 45)
    assert 1 <= line <= 40 and 0.4 <= p <= 0.6


def test_each_position_has_two_player_templates_in_band_at_10min():
    left = 80.0
    for pos in ("GK", "DF", "MF", "FW"):
        in_band = []
        for stat in ("touch", "save", "shot", "win_ball", "clearance", "pass_complete"):
            if POSITION_RATES_90[pos][stat] <= 0:
                continue
            for mode in ("window_s", "line"):
                _, p = tune_player(pos, stat, mode, left)
                if 0.4 <= p <= 0.6:
                    in_band.append((stat, mode))
        assert ("touch", "window_s") in in_band, pos
        assert len(in_band) >= 2, (pos, in_band)


def _params(t):
    out = {}
    for k, v in t.params.items():
        out[k] = (10 if k == "line" else 600) if v == "tuned" else v[0]
    out["team"] = "H"
    return out


def test_every_template_priced_with_all_options():
    state = _state(score={"H": 0, "A": 1})
    for t in load_bank().values():
        player = Player("p", "P", "H", "MF") if t.kind == "player" else None
        if t.positions and player and "MF" not in t.positions:
            player = Player("p", "P", "H", t.positions[0])
        probs = PRICERS[t.prob](t, _params(t), state, player)
        assert set(probs) >= set(t.options), t.id
        for o in t.options:
            assert 0.0 <= probs[o] <= 1.0, (t.id, o)
        if len(t.options) == 2 and t.prob != "poisson_first":
            assert sum(probs[o] for o in t.options) == pytest.approx(1.0), t.id


def test_goal_window_matches_poisson():
    t = load_bank()["goal_window"]
    p = PRICERS["poisson_count"](t, {"window": "10m"}, _state(), None)
    assert p["yes"] == pytest.approx(0.259, abs=0.002)


def test_half_and_match_windows_use_clock():
    t = load_bank()["goal_window"]
    p = PRICERS["poisson_count"](t, {"window": "half"}, _state(t=2400.0), None)
    assert p["yes"] == pytest.approx(p_at_least(2.7, 5, 1))
    pen = load_bank()["penalty_window"]
    p = PRICERS["poisson_count"](pen, {"window": "match"}, _state(t=600.0), None)
    assert p["yes"] == pytest.approx(0.234, abs=0.002)


def test_goals_over_subtracts_goals_already_scored():
    t = load_bank()["goals_over"]
    ev = [{"type": "goal", "team": "H", "t": 300.0, "half": 1, "player_id": "x"}]
    fresh = PRICERS["poisson_count"](t, {"window": "match", "line": 2}, _state(), None)
    after = PRICERS["poisson_count"](t, {"window": "match", "line": 2},
                                     _state(events=ev, score={"H": 1, "A": 0}), None)
    assert after["yes"] == pytest.approx(p_at_least(2.7, 80, 1))
    assert after["yes"] > fresh["yes"]


def test_first_of_keys_and_none():
    t = load_bank()["next_goal_team"]
    p = PRICERS["poisson_first"](t, {"window": "match"}, _state(), None)
    assert p["home"] == pytest.approx(p["away"]) and 0 < p["none"] < 0.1
    assert p["home"] + p["away"] + p["none"] == pytest.approx(1.0)


def test_instant_uses_defaults():
    t = load_bank()["next_pass_dir"]
    assert PRICERS["instant"](t, {"window_s": 15}, _state(), None) == INSTANT_DEFAULTS["next_pass_dir"]


def test_player_next_pass_from_position():
    t = load_bank()["player_next_pass_complete"]
    p = PRICERS["player"](t, {"window_s": 600}, _state(), Player("p", "P", "DF", "DF"))
    assert p["yes"] == pytest.approx(0.85) and p["no"] == pytest.approx(0.15)
    t = load_bank()["player_next_pass_long"]
    p = PRICERS["player"](t, {"window_s": 600}, _state(), Player("p", "P", "GK", "GK"))
    assert p["long"] == pytest.approx(0.55)


def test_player_stat_over_counts_since_half_start():
    t = load_bank()["player_stat_over"]
    pl = Player("p", "P", "H", "FW")
    ev = [{"type": "shot", "team": "H", "t": 100.0, "half": 1, "player_id": "p"},
          {"type": "shot", "team": "H", "t": 200.0, "half": 1, "player_id": "p"}]
    p = PRICERS["player"](t, {"line": 3}, _state(events=ev), pl)
    expect = p_at_least(POSITION_RATES_90["FW"]["shot"], (2700 - 600) / 60, 1)
    assert p["yes"] == pytest.approx(expect)
