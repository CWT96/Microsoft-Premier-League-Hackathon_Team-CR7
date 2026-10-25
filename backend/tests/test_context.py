from backend.bank.context import ctx_for
from backend.bank.loader import load_bank
from backend.bank.types import MatchState, Player


def _state(score, roster=None) -> MatchState:
    return MatchState(
        match_id="m", t=0.0, half=1, home="AAA", away="BBB",
        team_names={"AAA": "A", "BBB": "B"}, score=score, possession_team="BBB",
        events=[], open_template_ids=[], last_template_id=None,
        open_window_count=0, roster=roster or {},
    )


def test_ctx_basic_fields():
    t = load_bank()["team_wins"]
    p = Player("p9", "N", "AAA", "FW")
    ctx = ctx_for(t, {"team": "AAA"}, _state({"AAA": 0, "BBB": 1}), p)
    assert (ctx.home, ctx.away, ctx.possession_team) == ("AAA", "BBB", "BBB")
    assert ctx.trailing_team == "AAA"
    assert ctx.favourite == "p9" and ctx.favourite_position == "FW"
    assert ctx.team == "AAA"


def test_level_score_has_no_trailing_team_and_no_player():
    t = load_bank()["goal_window"]
    ctx = ctx_for(t, {}, _state({"AAA": 1, "BBB": 1}), None)
    assert ctx.trailing_team is None and ctx.favourite is None
    assert ctx.favourite_position is None and ctx.team is None


def test_gk_ids_first_goalkeeper_per_team():
    roster = {
        "a1": Player("a1", "x", "AAA", "DF"),
        "a2": Player("a2", "x", "AAA", "GK"),
        "a3": Player("a3", "x", "AAA", "GK"),
        "b1": Player("b1", "x", "BBB", "GK"),
    }
    ctx = ctx_for(load_bank()["goal_window"], {}, _state({"AAA": 0, "BBB": 0}, roster), None)
    assert ctx.gk_ids == {"AAA": "a2", "BBB": "b1"}
