"""Tests for the resolver primitives."""
from __future__ import annotations

import pytest

from backend.bank.loader import load_bank
from backend.bank.types import Window
from backend.resolve.primitives import Ctx, PRIMITIVES, event_matches, is_penalty, settle

CTX = Ctx(home="NBR", away="TGT", possession_team="NBR", trailing_team="TGT",
          favourite="p9", team="NBR", gk_ids={"NBR": "gkN", "TGT": "gkT"},
          favourite_position="FW")
WIN = Window(100, 700, None)


def ev(t, type_="shot", team="NBR", **kw):
    return {"id": f"e{t}", "t": t, "half": 1, "team": team, "type": type_, **kw}


def test_goal_in_window_yes_early():
    spec = {"primitive": "count_at_least", "filter": {"type": "goal"}, "n": 1}
    assert settle(spec, [ev(200, "goal")], WIN, CTX, closed=False) == "yes"


def test_goal_in_window_no_when_closed():
    spec = {"primitive": "count_at_least", "filter": {"type": "goal"}, "n": 1}
    assert settle(spec, [], WIN, CTX, closed=True) == "no"


def test_pending_returns_none():
    spec = {"primitive": "count_at_least", "filter": {"type": "goal"}, "n": 1}
    assert settle(spec, [], WIN, CTX, closed=False) is None


def test_event_after_end_closes_window_and_is_ignored():
    spec = {"primitive": "count_at_least", "filter": {"type": "goal"}, "n": 1}
    assert settle(spec, [ev(800, "goal")], WIN, CTX, closed=False) == "no"


def test_stoppage_goal_counts_for_match_window():
    spec = {"primitive": "count_at_least", "filter": {"type": "goal"}, "n": 2}
    events = [ev(3000, "goal"), ev(5500, "goal"), ev(5580, "full_end")]
    assert settle(spec, events, Window(0, None, "full_end"), CTX, closed=False) == "yes"
    assert settle(spec, events[:1] + events[2:], Window(0, None, "full_end"), CTX, closed=False) == "no"


def test_events_before_start_ignored():
    spec = {"primitive": "count_at_least", "filter": {"type": "goal"}, "n": 1}
    assert settle(spec, [ev(100, "goal")], WIN, CTX, closed=False) is None


def test_n_line_and_negate():
    spec = {"primitive": "count_at_least", "filter": {"type": "foul"}, "n": "line"}
    evs = [ev(150, "foul"), ev(160, "foul")]
    assert settle(spec, evs, WIN, CTX, False, {"line": 2}) == "yes"
    assert settle(spec, evs, WIN, CTX, False, {"line": 3}) is None
    neg = {"primitive": "count_at_least", "filter": {"type": "shot"}, "n": 1, "negate": True}
    assert settle(neg, [ev(150)], WIN, CTX, False) == "no"
    assert settle(neg, [], WIN, CTX, True) == "yes"


def test_compare_counts_tie_is_no():
    spec = {"primitive": "compare_counts", "filter_a": {"type": "shot", "team": "possession"},
            "filter_b": {"type": "shot", "team": "opponent"}}
    evs = [ev(150), ev(160), ev(170, team="TGT"), ev(180, team="TGT")]
    assert settle(spec, evs, WIN, CTX, True) == "no"
    assert settle(spec, evs + [ev(190)], WIN, CTX, True) == "yes"
    assert settle(spec, evs + [ev(190)], WIN, CTX, False) is None


def test_first_of():
    spec = {"primitive": "first_of", "filter_a": {"type": "shot", "team": "home"},
            "filter_b": {"type": "shot", "team": "away"}, "options": ["home", "away"]}
    assert settle(spec, [ev(150, team="TGT"), ev(160)], WIN, CTX, False) == "away"
    assert settle(spec, [], WIN, CTX, False) is None


def test_first_of_neither_is_void():
    spec = {"primitive": "first_of", "filter_a": {"type": "shot", "team": "home"},
            "filter_b": {"type": "shot", "team": "away"}, "options": ["home", "away"]}
    assert settle(spec, [], WIN, CTX, True) == "void"


def test_penalty_detection():
    assert is_penalty({"type": "foul", "start": [10, 34]})
    assert not is_penalty({"type": "foul", "start": [30, 34]})
    assert not is_penalty({"type": "foul", "start": [10, 5]})
    assert not is_penalty({"type": "foul"})
    assert not is_penalty({"type": "pass", "start": [10, 34]})


def test_equalise_yes_when_level():
    spec = {"primitive": "final_state", "predicate": "equalise"}
    evs = [ev(50, "goal"), ev(300, "goal", team="TGT")]
    assert settle(spec, evs, WIN, CTX, False) == "yes"
    assert settle(spec, evs[:1], WIN, CTX, False) is None
    assert settle(spec, evs[:1], WIN, CTX, True) == "no"


def test_both_score_and_team_wins():
    both = {"primitive": "final_state", "predicate": "both_score"}
    evs = [ev(50, "goal"), ev(300, "goal", team="TGT")]
    assert settle(both, evs, WIN, CTX, False) == "yes"
    assert settle(both, evs[:1], WIN, CTX, False) is None
    assert settle(both, evs[:1], WIN, CTX, True) == "no"
    wins = {"primitive": "final_state", "predicate": "team_wins"}
    assert settle(wins, evs[:1], WIN, CTX, False) is None
    assert settle(wins, evs[:1], WIN, CTX, True) == "yes"
    assert settle(wins, evs, WIN, CTX, True) == "no"


def test_next_pass_long_no_pass_is_short():
    spec = {"primitive": "next_matching", "filter": {"type": "pass", "player": "favourite"},
            "test": {"min_length_m": 25}, "no_event": "short"}
    assert settle(spec, [], WIN, CTX, True) == "short"
    assert settle(spec, [], WIN, CTX, False) is None
    p = ev(150, "pass", player_id="p9", start=[10, 10], end=[40, 10])
    assert settle(spec, [p], WIN, CTX, False) == "long"


def test_next_matching_complete_and_direction():
    comp = {"primitive": "next_matching", "filter": {"type": "pass"},
            "test": {"complete": True}, "no_event": "void"}
    assert settle(comp, [ev(150, "pass", outcome="incomplete")], WIN, CTX, False) == "no"
    assert settle(comp, [], WIN, CTX, True) == "void"
    d = {"primitive": "next_matching", "filter": {"type": "pass"},
         "test": {"dx_dead_zone_m": 5}, "no_event": "void"}
    for end, want in (([30, 10], "forward"), ([22, 10], "sideways"), ([10, 10], "backward")):
        p = ev(150, "pass", start=[20, 10], end=end)
        assert settle(d, [p], WIN, CTX, False) == want



def test_next_matching_forward_is_two_way():
    fwd = {"primitive": "next_matching", "filter": {"type": "pass"},
           "test": {"forward": True}, "no_event": "no"}
    for end, want in (([21, 10], "yes"), ([20, 30], "no"), ([10, 10], "no")):
        p = ev(150, "pass", start=[20, 10], end=end)
        assert settle(fwd, [p], WIN, CTX, False) == want
    assert settle(fwd, [ev(150, "pass")], WIN, CTX, False) == "no"
    assert settle(fwd, [], WIN, CTX, True) == "no"

def test_filters():
    assert event_matches(ev(1, "pass", outcome="complete"), {"type": ["pass"], "outcome": "complete"}, CTX)
    assert event_matches(ev(1, team="TGT"), {"team": "opponent"}, CTX)
    assert event_matches(ev(1, team="TGT"), {"team": "param_opp"}, CTX)
    assert not event_matches(ev(1, team="TGT"), {"team": "param"}, CTX)
    assert event_matches(ev(1, team="TGT"), {"team": "trailing"}, CTX)
    assert event_matches(ev(1, player_id="gkN"), {"player": "team_gk"}, CTX)
    assert not event_matches(ev(1, player_id="gkT"), {"player": "team_gk"}, CTX)
    assert event_matches(ev(1, "tackle", outcome="won", player_id="p9"),
                         {"player": "favourite", "stat": "win_ball"}, CTX)
    assert event_matches(ev(1, "interception", player_id="p9"), {"stat": "win_ball"}, CTX)
    assert event_matches(ev(1, "carry", player_id="p9"), {"player": "favourite", "stat": "touch"}, CTX)
    assert event_matches(ev(1, "pass", start=[10, 5], end=[20, 60]), {"derived": "switch"}, CTX)
    assert event_matches(ev(1, "pass", start=[10, 60], end=[20, 5]), {"derived": "switch"}, CTX)
    assert not event_matches(ev(1, "pass", start=[10, 30], end=[20, 40]), {"derived": "switch"}, CTX)
    assert event_matches(ev(1, "pass", end=[80, 5]), {"end_x_gt": 70}, CTX)
    assert not event_matches(ev(1, "pass"), {"end_x_gt": 70}, CTX)


def test_stat_by_position():
    spec = {"primitive": "count_at_least", "filter": {"player": "favourite"}, "n": 1,
            "stat_by_position": {"GK": "save", "FW": "shot"}}
    evs = [ev(150, "shot", player_id="p9")]
    assert settle(spec, evs, WIN, CTX, False) == "yes"
    gk = Ctx(**{**CTX.__dict__, "favourite_position": "GK"})
    assert settle(spec, evs, WIN, gk, False) is None


def test_primitives_set():
    assert set(PRIMITIVES) == {"count_at_least", "compare_counts", "first_of", "final_state", "next_matching"}


@pytest.mark.parametrize("tid", sorted(load_bank()))
def test_every_template_spec_accepted_on_empty_events(tid):
    spec = load_bank()[tid].resolve
    params = {"line": 2, "team": "NBR"}
    assert settle(spec, [], WIN, CTX, False, params) in (None, "yes", "no", "void", "short", "long")
    assert settle(spec, [], WIN, CTX, True, params) is not None
