"""Generator stub: fills moments from the 10 catalog templates, contract-valid and deterministic."""

import pytest

from backend.agents.question_generator_stub import CATALOG_IDS, propose
from backend.app.models import dump
from backend.core.types import DetectorState, Moment, Player, Roster, player_qid
from backend.tests.contract_utils import validate


def _state(**kw) -> DetectorState:
    base = dict(
        t=500.0, half=1, mode="live", break_kind=None, score_home=0, score_away=0,
        situation="open_play", dull_score=0.7, threat=0.1, movement=None, pressure_m=None,
        ball_xy=(50.0, 34.0), ball_zone="middle_third", possession_team="NBR",
        seconds_since_shot=120.0, last_attack_end_t=None, attack_active=False,
        restart_pending=False, restart_taken=False)
    base.update(kw)
    return DetectorState(**base)


def _moment(mid: str = "q-1", category: str = "instant", kind: str = "instant",
            situation: str = "calm_play", **kw) -> Moment:
    base = dict(moment_id=mid, kind=kind, category=category, open_at=500.0, lock_at=506.0,
                lock_rule="fixed", situation=situation, cost=0.2, half=1, tone=None)
    base.update(kw)
    return Moment(**base)


def _roster() -> Roster:
    players = (Player("nbr-1", "Ward", "NBR", "GK", 1), Player("nbr-4", "Diaz", "NBR", "DF", 4),
               Player("nbr-8", "Mensah", "NBR", "MF", 8), Player("nbr-9", "Okoro", "NBR", "FW", 9),
               Player("cal-9", "Reyes", "CAL", "FW", 9))
    return Roster("NBR", "CAL", "Northbridge", "Caldera", players)


def _check(q) -> dict:
    payload = dump(q)
    validate("question", payload)
    assert len(payload["options"]) == 2
    assert q.hint_available is True
    assert q.template_id in CATALOG_IDS
    return payload


def test_instant_questions_validate_for_many_moments() -> None:
    seen = set()
    for i in range(40):
        for sit in ("calm_play", "stalled_near_box", "throw_in", "goal_kick", "set_piece_wait"):
            q = propose(_state(), _moment(f"q-{i}", situation=sit), None, _roster())
            assert q is not None
            _check(q)
            seen.add(q.template_id)
    assert seen == {"next_pass_dir", "keep_ball", "reach_final_third", "shot_soon"}


def test_fields_copied_from_moment() -> None:
    m = _moment("q-9", situation="throw_in", lock_rule="on_restart", tone="Throw-in!",
                open_at=700.0, lock_at=730.0)
    q = propose(_state(), m, None, _roster())
    assert (q.qid, q.open_at, q.lock_at, q.lock_rule, q.situation, q.tone, q.category) == (
        "q-9", 700.0, 730.0, "on_restart", "throw_in", "Throw-in!", "instant")


def test_next_pass_dir_uses_forward_backward_and_dead_zone() -> None:
    qs = [propose(_state(), _moment(f"q-{i}", situation="throw_in"), None, _roster())
          for i in range(20)]
    npd = [q for q in qs if q.template_id == "next_pass_dir"]
    assert npd
    for q in npd:
        assert [o.id for o in q.options] == ["forward", "backward"]
        assert q.params == {"dead_zone_m": 5}
        assert q.text == "Next pass: forward or back?"


def test_stalled_near_box_asks_about_a_shot() -> None:
    q = propose(_state(situation="stalled_near_box"),
                _moment(situation="stalled_near_box"), None, _roster())
    assert q.template_id == "shot_soon" and q.text == "Shot in the next 60s?"


def test_no_possession_only_shot_soon() -> None:
    for i in range(20):
        q = propose(_state(possession_team=None, ball_zone=None),
                    _moment(f"q-{i}"), None, _roster())
        assert q.template_id == "shot_soon"


def test_deterministic() -> None:
    a = propose(_state(), _moment("q-5"), None, _roster())
    b = propose(_state(), _moment("q-5"), None, _roster())
    assert a == b


def test_forecast_questions_validate() -> None:
    kickoff, ht = set(), set()
    for i in range(40):
        for kind, bucket in (("kickoff_stack", kickoff), ("ht_stack", ht)):
            m = _moment(f"f-{i}", category="forecast", kind=kind, situation="kickoff",
                        lock_at=560.0)
            q = propose(_state(), m, None, _roster())
            assert q is not None
            p = _check(q)
            assert p["resolve_by"] == "FT" and q.category == "forecast"
            bucket.add(q.template_id)
    assert kickoff == {"goals_over", "team_wins"}
    assert ht == {"goals_over", "team_wins", "card_second_half", "second_half_goal"}


def test_second_half_templates_only_at_half_time_stack() -> None:
    for kind in ("kickoff_stack", "water_break", "instant", "player_instant"):
        for i in range(40):
            m = _moment(f"f-{i}", category="forecast", kind=kind, half=2)
            q = propose(_state(half=2), m, None, _roster())
            assert q.template_id in {"goals_over", "team_wins"}


def test_unknown_position_returns_none_and_logs(caplog) -> None:
    import logging
    from types import SimpleNamespace
    ghost = SimpleNamespace(player_id="x-1", name="Ghost", position="XX")
    roster = SimpleNamespace(by_id=lambda: {"x-1": ghost})
    m = _moment("p-9", category="player", kind="player_instant")
    with caplog.at_level(logging.WARNING):
        assert propose(_state(), m, "x-1", roster) is None
    assert "p-9" in caplog.text


def test_validation_failure_returns_none_and_logs(caplog) -> None:
    import logging
    long_name = Player("nbr-9", "N" * 200, "NBR", "FW", 9)
    roster = Roster("NBR", "CAL", "Northbridge", "Caldera", (long_name,))
    pm = _moment("p-bad", category="player", kind="player_instant")
    with caplog.at_level(logging.WARNING):
        assert propose(_state(), pm, "nbr-9", roster) is None  # text over 140 chars
    assert "p-bad" in caplog.text


def test_goals_over_line_is_above_current_goals() -> None:
    for i in range(60):
        m = _moment(f"f-{i}", category="forecast", kind="water_break", half=2)
        q = propose(_state(half=2, score_home=3, score_away=2), m, None, _roster())
        if q.template_id == "goals_over":
            assert q.params["line"] >= 6
            assert q.text == f"{q.params['line']} or more goals in total?"
            return
    pytest.fail("goals_over never chosen")


def test_team_wins_names_the_team() -> None:
    for i in range(60):
        m = _moment(f"f-{i}", category="forecast", kind="kickoff_stack")
        q = propose(_state(), m, None, _roster())
        if q.template_id == "team_wins":
            assert q.params == {"team": "NBR"}
            assert q.text == "Will Northbridge win?"
            return
    pytest.fail("team_wins never chosen")


@pytest.mark.parametrize("pid,stat", [("nbr-1", "saves"), ("nbr-4", "clearances"),
                                      ("nbr-8", "completed passes"), ("nbr-9", "shots")])
def test_player_stat_question_by_position(pid, stat) -> None:
    m = _moment("p-1", category="player", kind="kickoff_stack", situation="kickoff")
    q = propose(_state(), m, pid, _roster())
    p = _check(q)
    assert q.qid == player_qid("p-1", pid) and q.player_id == pid and q.category == "player"
    assert q.template_id == "player_stat_over" and q.params["stat"] == stat
    assert isinstance(q.params["line"], int) and p["resolve_by"] == "HT"
    assert q.text.startswith("Will ") and "this half?" in q.text


def test_player_stat_resolve_by_halftime_stack_is_full_time() -> None:
    m = _moment("p-2", category="player", kind="ht_stack", situation="half_time")
    assert propose(_state(), m, "nbr-9", _roster()).resolve_by == "FT"


def test_player_touch_window() -> None:
    m = _moment("p-3", category="player", kind="player_instant")
    q = propose(_state(), m, "nbr-4", _roster())
    p = _check(q)
    assert q.template_id == "player_touch_window" and q.params["window_s"] > 0
    assert p["resolve_by"] == m.lock_at + q.params["window_s"]
    assert "Diaz" in q.text


def test_player_question_needs_a_known_player() -> None:
    m = _moment("p-4", category="player", kind="player_instant")
    assert propose(_state(), m, None, _roster()) is None
    assert propose(_state(), m, "ghost-1", _roster()) is None
    assert propose(_state(), m, "nbr-9", None) is None


def test_works_without_roster_for_shared_questions() -> None:
    q = propose(_state(), _moment("q-2"), None)
    assert q is not None
    _check(q)
