"""settle_candidate: a Candidate settles on its own, with the context it was asked in."""
from dataclasses import replace

from backend.bank.candidates import build_candidates
from backend.bank.loader import load_bank
from backend.bank.settle import settle_candidate
from backend.bank.types import MatchState, Player

BANK = load_bank()
MF = Player("p8", "Eight", "AAA", "MF")
GK_A = Player("g1", "Keeper A", "AAA", "GK")
GK_B = Player("g2", "Keeper B", "BBB", "GK")


def _state(t=1200.0, poss="AAA") -> MatchState:
    return MatchState(
        match_id="m", t=t, half=1, home="AAA", away="BBB",
        team_names={"AAA": "A", "BBB": "B"}, score={"AAA": 0, "BBB": 0},
        possession_team=poss, events=[], open_template_ids=[], last_template_id=None,
        open_window_count=0, roster={"p8": MF, "g1": GK_A, "g2": GK_B},
    )


def _ev(t, team, typ="pass", pid=None, **kw):
    return {"id": f"e{t}", "t": float(t), "half": 1, "team": team, "type": typ,
            "player_id": pid, **kw}


def _cand(kind, tid, **match):
    got = [c for c in build_candidates(kind, _state(), BANK, 1200)
           if c.template_id == tid and all(c.params.get(k) == v for k, v in match.items())]
    assert len(got) == 1, [c.params for c in got]
    return got[0]


def test_candidate_carries_the_context_it_was_asked_in():
    c = _cand("instant", "keep_ball")
    assert c.context.possession_team == "AAA"
    assert c.context.team == "AAA"


def test_keep_ball_is_no_when_opponent_wins_the_ball():
    c = _cand("instant", "keep_ball")  # lock 1206, window to 1221
    # Only team B acts after the lock: a Ctx rebuilt from the later state
    # (possession B, "opponent" = A) would wrongly give "yes".
    events = [_ev(1210, "BBB", "interception"), _ev(1214, "BBB")]
    assert settle_candidate(c, events, BANK) == "no"


def test_keep_ball_is_yes_when_team_keeps_it():
    c = _cand("instant", "keep_ball")
    events = [_ev(1208, "AAA"), _ev(1215, "AAA"), _ev(1230, "BBB")]
    assert settle_candidate(c, events, BANK) == "yes"


def test_more_shots_window_keeps_the_team_asked_about():
    c = _cand("window", "more_shots_window", window="10m")  # lock 1206, ends 1806
    assert c.params["team"] == "AAA"
    events = [_ev(1300, "AAA", "shot"), _ev(1400, "AAA", "shot"),
              _ev(1500, "BBB", "shot"), _ev(1600, "BBB"), _ev(1900, "BBB")]
    assert settle_candidate(c, events, BANK) == "yes"


def test_back_to_keeper_means_the_keeper_of_the_team_asked_about():
    c = _cand("instant", "back_to_keeper")  # lock 1206, window to 1236
    events = [_ev(1210, "BBB", pid="g2"), _ev(1215, "BBB"), _ev(1240, "AAA")]
    assert settle_candidate(c, events, BANK) == "no"
    events = [_ev(1210, "BBB"), _ev(1220, "AAA", pid="g1")]
    assert settle_candidate(c, events, BANK) == "yes"


def test_next_pass_dir_text_matches_shown_options():
    c = _cand("instant", "next_pass_dir")
    assert c.options == ["backward", "forward"]
    assert c.text == "Next pass: forward or back?"


def test_result_not_among_shown_options_is_void():
    c = _cand("instant", "next_pass_dir")  # lock 1206
    sideways = _ev(1210, "AAA", start=[50.0, 30.0], end=[52.0, 40.0])
    assert settle_candidate(c, [sideways], BANK) == "void"
    forward = _ev(1210, "AAA", start=[50.0, 30.0], end=[65.0, 30.0])
    assert settle_candidate(c, [forward], BANK) == "forward"


def test_player_result_outside_options_is_an_error():
    import pytest
    pl = [c for c in build_candidates("player", replace(_state(), events=[_ev(1150, "AAA", pid="p8")]),
                                      BANK, 1200, MF)]
    assert pl
    bad = replace(pl[0], options=["nope", "never"])
    with pytest.raises(AssertionError):
        settle_candidate(bad, [], BANK, closed=True)
