from dataclasses import replace

from backend.bank.guards import GUARDS, band_for, window_for
from backend.bank.loader import load_bank
from backend.bank.types import MatchState, Player, Window
from backend.model.poisson import PRICERS

BANK = load_bank()
FW = Player("p9", "Nine", "AAA", "FW")
GK = Player("g1", "Keeper", "AAA", "GK")


def _state(t=600.0, half=1, score=None, events=None, open_ids=None, last=None,
           open_windows=0) -> MatchState:
    return MatchState(
        match_id="m", t=t, half=half, home="AAA", away="BBB",
        team_names={"AAA": "A", "BBB": "B"}, score=score or {"AAA": 0, "BBB": 0},
        possession_team="AAA", events=events or [], open_template_ids=open_ids or [],
        last_template_id=last, open_window_count=open_windows,
        roster={"p9": FW, "g1": GK},
    )


def _run(name, tid, params, state, player=None, probs=None):
    return GUARDS[name](BANK[tid], params, state, player, probs or {})


def test_every_bank_guard_is_registered():
    used = {g for t in BANK.values() for g in t.guards}
    assert used <= set(GUARDS)
    assert set(GUARDS) == used


def test_10m_window_at_40min_rejected():
    assert not _run("fits_clock", "goal_window", {"window": "10m"}, _state(t=2400))
    assert _run("fits_clock", "goal_window", {"window": "10m"}, _state(t=1800))


def test_half_window_needs_ten_minutes_left_and_match_after_75_rejected():
    assert _run("fits_clock", "goal_window", {"window": "half"}, _state(t=2100))
    assert not _run("fits_clock", "goal_window", {"window": "half"}, _state(t=2101))
    assert not _run("fits_clock", "goal_window", {"window": "half"}, _state(t=5000, half=2))
    assert _run("fits_clock", "team_wins", {"window": "match"}, _state(t=4500, half=2))
    assert not _run("fits_clock", "team_wins", {"window": "match"}, _state(t=4560, half=2))


def test_window_s_and_default_half_for_player_stat_over():
    assert _run("fits_clock", "player_touch_window", {"window_s": 300}, _state(t=2000))
    assert not _run("fits_clock", "player_touch_window", {"window_s": 300}, _state(t=2400))
    assert not _run("fits_clock", "player_stat_over", {"line": 2}, _state(t=2200))


def test_goals_over_2_skipped_after_2_goals():
    goals = [{"id": f"g{i}", "t": 100.0 * i, "half": 1, "team": "AAA", "type": "goal"}
             for i in (1, 2)]
    p = {"window": "match", "line": 2}
    assert not _run("not_decided", "goals_over", p, _state(events=goals, score={"AAA": 2, "BBB": 0}))
    assert _run("not_decided", "goals_over", p, _state(events=goals[:1]))


def test_three_nil_team_wins_fails_normal_band():
    st = _state(t=3000, score={"AAA": 3, "BBB": 0})
    t = BANK["team_wins"]
    probs = PRICERS[t.prob](t, {"window": "match", "team": "AAA"}, st, None)
    assert probs["yes"] > 0.85
    assert not _run("prob_band", "team_wins", {"team": "AAA"}, st, probs=probs)


def test_normal_band_edges():
    assert _run("prob_band", "goal_window", {}, _state(), probs={"yes": 0.8, "no": 0.2})
    assert not _run("prob_band", "goal_window", {}, _state(), probs={"yes": 0.9, "no": 0.1})
    assert not _run("prob_band", "goal_window", {}, _state(), probs={"yes": 0.1, "no": 0.9})


def test_penalty_rare_band_passes_match_window_early_only():
    t = BANK["penalty_window"]
    p = {"window": "match"}
    early, late = _state(t=600), _state(t=1800)
    pe = PRICERS[t.prob](t, p, early, None)
    pl = PRICERS[t.prob](t, p, late, None)
    assert GUARDS["prob_band"](t, p, early, None, pe)
    assert not GUARDS["prob_band"](t, p, late, None, pl)


def test_three_option_top_two_renormalised():
    t = replace(BANK["next_pass_complete"], options=["a", "b", "c"])
    probs = {"a": 0.5, "b": 0.3, "c": 0.2, "none": 0.0}
    assert GUARDS["prob_band"](t, {}, _state(), None, probs)  # 0.5/0.8 = 0.625
    probs = {"a": 0.9, "b": 0.05, "c": 0.05}
    assert not GUARDS["prob_band"](t, {}, _state(), None, probs)  # 0.9/0.95 > 0.85


def test_player_band_rejects_p_0_33():
    p = {"window_s": 300}
    assert not _run("prob_band", "player_touch_window", p, _state(), FW, {"yes": 0.33, "no": 0.67})
    assert _run("prob_band", "player_touch_window", p, _state(), FW, {"yes": 0.45, "no": 0.55})
    assert band_for(BANK["player_touch_window"]) == (0.40, 0.60)
    assert band_for(BANK["penalty_window"]) == (0.20, 0.80)


def test_sent_off_player_inactive():
    ev = [{"id": "e1", "t": 500.0, "half": 1, "team": "AAA", "type": "pass", "player_id": "p9"}]
    st = _state(t=600, events=ev)
    assert _run("player_active", "player_touch_window", {}, st, FW)
    red = ev + [{"id": "e2", "t": 550.0, "half": 1, "team": "AAA", "type": "card",
                 "outcome": "red", "player_id": "p9"}]
    assert not _run("player_active", "player_touch_window", {}, _state(t=600, events=red), FW)
    assert not _run("player_active", "player_touch_window", {}, _state(t=1200, events=ev), FW)


def test_gk_not_offered_shot_template():
    assert not _run("position_fit", "player_shot_window", {}, _state(), GK)
    assert _run("position_fit", "player_shot_window", {}, _state(), FW)
    assert _run("position_fit", "player_save_window", {}, _state(), GK)


def test_equaliser_needs_lead_of_one():
    p = {"window": "10m", "team": "BBB"}
    assert not _run("needs_lead_of_one", "equaliser_window", p, _state(score={"AAA": 0, "BBB": 0}))
    assert _run("needs_lead_of_one", "equaliser_window", p, _state(score={"AAA": 1, "BBB": 0}))
    assert not _run("needs_lead_of_one", "equaliser_window", p, _state(score={"AAA": 2, "BBB": 0}))
    assert not _run("needs_lead_of_one", "equaliser_window",
                    {"window": "10m", "team": "AAA"}, _state(score={"AAA": 1, "BBB": 0}))


def test_no_repeat_last_and_open():
    assert _run("no_repeat", "goal_window", {}, _state())
    assert not _run("no_repeat", "goal_window", {}, _state(last="goal_window"))
    assert not _run("no_repeat", "goal_window", {}, _state(open_ids=["goal_window"]))


def test_max_open_windows():
    assert _run("max_open_windows", "goal_window", {}, _state(open_windows=1))
    assert not _run("max_open_windows", "goal_window", {}, _state(open_windows=2))


def test_p_none_below_035():
    assert _run("p_none_below_0.35", "next_goal_team", {}, _state(),
                probs={"home": 0.4, "away": 0.4, "none": 0.2})
    assert not _run("p_none_below_0.35", "next_goal_team", {}, _state(),
                    probs={"home": 0.3, "away": 0.3, "none": 0.4})


def test_v2_instant_has_no_band():
    assert band_for(BANK["next_pass_dir"]) is None
    assert GUARDS["prob_band"](BANK["next_pass_dir"], {}, _state(), None, {"forward": 0.99})


def test_window_for():
    st = _state(t=600)
    assert window_for(BANK["goal_window"], {"window": "5m"}, st, 610) == Window(610, 910, None)
    assert window_for(BANK["goal_window"], {"window": "half"}, st, 610) == Window(610, None, "half_end")
    assert window_for(BANK["goals_over"], {"window": "match"}, st, 610) == Window(0, None, "full_end")
    assert window_for(BANK["shot_soon"], {"window_s": 60}, st, 610) == Window(610, 670, None)
    h2 = _state(t=3000, half=2)
    assert window_for(BANK["player_stat_over"], {"line": 2}, h2, 3010) == Window(2700, None, "half_end")
    assert window_for(BANK["player_stat_over"], {"line": 2}, st, 610) == Window(0, None, "half_end")
