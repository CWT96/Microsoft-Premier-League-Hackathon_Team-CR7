import pytest

from dataclasses import replace

from backend.bank.candidates import build_candidates
from backend.bank.loader import load_bank
from backend.model.poisson import PRICERS
from backend.bank.types import CATEGORY, MatchState, Player

BANK = load_bank()
MF = Player("p8", "Eight", "AAA", "MF")
GK = Player("g1", "Keeper", "AAA", "GK")


def _state(t=1200.0, half=1, score=None, poss="AAA", events=None, open_ids=None,
           last=None, open_windows=0) -> MatchState:
    return MatchState(
        match_id="m", t=t, half=half, home="AAA", away="BBB",
        team_names={"AAA": "A", "BBB": "B"}, score=score or {"AAA": 0, "BBB": 0},
        possession_team=poss, events=events or [], open_template_ids=open_ids or [],
        last_template_id=last, open_window_count=open_windows,
        roster={"p8": MF, "g1": GK},
    )


def _touch(pid, t):
    return {"id": f"e{t}", "t": t, "half": 1, "team": "AAA", "type": "pass", "player_id": pid}


def only(cands, tid):
    got = [c for c in cands if c.template_id == tid]
    assert len(got) == 1, [c.params for c in got]
    return got[0]


def test_dull_midfield_at_20min_has_instant_window_player_candidates():
    s = _state(events=[_touch("p8", 1150)])
    inst = build_candidates("instant", s, BANK, 1200)
    assert "next_pass_dir" in {c.template_id for c in inst}
    assert all(c.kind == "instant" for c in inst)
    assert "goal_window" in {c.template_id for c in build_candidates("window", s, BANK, 1200)}
    pl = build_candidates("player", s, BANK, 1200, MF)
    assert "player_touch_window" in {c.template_id for c in pl}
    assert all(c.player_id == "p8" for c in pl)


def test_window_candidates_at_80min_exclude_20m_and_match():
    s = _state(t=4800.0, half=2)
    for c in build_candidates("window", s, BANK, 4800):
        assert c.params.get("window") not in ("20m", "match")


def test_next_pass_dir_shows_top2_renormalised():
    c = only(build_candidates("instant", _state(), BANK, 1200), "next_pass_dir")
    assert c.options == ["backward", "forward"]
    assert c.p["backward"] == pytest.approx(0.44 / 0.85, abs=1e-3)
    assert sum(c.p.values()) == pytest.approx(1.0)
    assert c.resolve_by == 1200 + 6 + 15


def test_penalty_offered_once_with_shortest_passing_window(monkeypatch):
    monkeypatch.setitem(PRICERS, "poisson_count", lambda *a: {"yes": 0.5, "no": 0.5})
    cands = build_candidates("window", _state(t=600.0), BANK, 600)
    pens = [c for c in cands if c.template_id == "penalty_window"]
    assert len(pens) == 1
    assert pens[0].params["window"] == "half"


def test_goalkeeper_gets_only_gk_templates():
    s = _state(events=[_touch("g1", 1150)])
    ids = {c.template_id for c in build_candidates("player", s, BANK, 1200, GK)}
    assert "player_save_window" in ids
    assert not ids & {"player_shot_window", "player_win_ball_window"}
    assert build_candidates("player", s, BANK, 1200, None) == []


def test_candidate_category_mapping():
    s = _state(events=[_touch("p8", 1150)])
    for kind in ("instant", "window", "player"):
        for c in build_candidates(kind, s, BANK, 1200, MF if kind == "player" else None):
            assert c.category == CATEGORY[kind]
    assert CATEGORY["window"] == "forecast"


def test_team_expansion_and_resolve_by():
    s = _state(poss=None)
    cands = build_candidates("instant", s, BANK, 1200)
    assert not [c for c in cands if c.template_id == "keep_ball"]
    s = _state(t=600.0)
    w = build_candidates("window", s, BANK, 600)
    g = [c for c in w if c.template_id == "goal_window"]
    for c in g:
        if c.params["window"] == "half":
            assert c.resolve_by == "HT"
        if c.params["window"] == "5m":
            assert c.resolve_by == 606 + 300
    assert {c.params["team"] for c in w if c.template_id == "team_wins"} <= {"AAA", "BBB"}
    for c in w:
        if c.template_id == "team_wins":
            assert c.resolve_by == "FT"


def test_player_stat_over_sets_stat_and_tuned_line():
    s = _state(events=[_touch("p8", 1150)])
    c = [x for x in build_candidates("player", s, BANK, 1200, MF)
         if x.template_id == "player_stat_over"]
    assert c
    for x in c:
        assert x.params["stat"] == "pass_complete" and isinstance(x.params["line"], int)


def test_player_template_with_no_stat_for_position_is_skipped():
    t = BANK["player_stat_over"]
    res = {**t.resolve, "stat_by_position": {}}
    bank = {"player_stat_over": replace(t, resolve=res)}
    s = _state(events=[_touch("p8", 1150)])
    assert build_candidates("player", s, bank, 1200, MF) == []


def test_player_stat_over_line_counts_passes_already_made():
    passes = [{"id": f"p{i}", "t": 100.0 + 50 * i, "half": 1, "team": "AAA", "type": "pass",
               "outcome": "complete", "player_id": "p8"} for i in range(20)]
    s = _state(events=passes)
    c = only(build_candidates("player", s, BANK, 1200, MF), "player_stat_over")
    assert c.params["stat"] == "pass_complete"
    assert c.params["line"] > 20
    assert 0.40 <= c.p["yes"] <= 0.60


def test_forward_gets_shot_window_at_20min():
    fw = Player("p9", "Nine", "AAA", "FW")
    s = replace(_state(events=[_touch("p9", 1150)]), roster={"p8": MF, "g1": GK, "p9": fw})
    c = only(build_candidates("player", s, BANK, 1200, fw), "player_shot_window")
    assert 1206 + c.params["window_s"] <= 2700
    assert 0.40 <= c.p["yes"] <= 0.60


def test_who_shoots_first_skipped_when_no_shot_is_likely(monkeypatch):
    assert "p_none_below_0.35" in BANK["who_shoots_first"].guards
    monkeypatch.setitem(PRICERS, "poisson_first",
                        lambda *a: {"home": 0.3, "away": 0.3, "none": 0.4})
    ids = {c.template_id for c in build_candidates("window", _state(), BANK, 1200)}
    assert "who_shoots_first" not in ids
