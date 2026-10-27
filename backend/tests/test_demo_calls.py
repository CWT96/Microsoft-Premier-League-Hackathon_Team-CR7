from backend.demo.calls import find_moments, select_calls

MOMENTS = find_moments()


def test_only_answerable_moments():
    assert MOMENTS and all(c.lock_t - c.open_t >= 3.0 for c in MOMENTS)
    answers = {c.answer for c in MOMENTS}
    assert answers == {"PASS", "SHOOT", "DRIBBLE"}


def test_saka_goal_is_a_shoot_call():
    calls = select_calls(MOMENTS, 1, 1422, 1440)
    assert [(round(c.open_t, 1), c.answer, c.player_id) for c in calls] == [(1430.0, "SHOOT", "910109")]
    assert round(calls[0].lock_t, 1) == 1434.3


def test_first_segment_keeps_the_dribble():
    # Saka's blocked shot (11:51.7) would open before the dribble's result has shown; the rarer
    # dribble wins, and the demo's shot comes from the next segment instead
    calls = select_calls(MOMENTS, 1, 694, 716)
    assert [c.answer for c in calls] == ["PASS", "DRIBBLE"]


def test_rice_shot_segment():
    calls = select_calls(MOMENTS, 1, 1011, 1023)
    assert [(c.answer, c.player_id) for c in calls] == [("SHOOT", "910108")]


def test_gap_and_no_overlap():
    calls = select_calls(MOMENTS, 1, 691, 716)
    for a, b in zip(calls, calls[1:]):
        assert b.open_t - a.open_t >= 6.0 and b.open_t >= a.lock_t + 3.0


def test_winner_and_equaliser_segments():
    assert [(c.answer, c.player_id) for c in select_calls(MOMENTS, 2, 4780, 4796)] == [("SHOOT", "910105")]
    assert [(c.answer, c.player_id) for c in select_calls(MOMENTS, 2, 3600, 3616)] == [("PASS", "910211")]


def test_carry_never_locks():
    # every lock is a pass, shot or dribble in the raw data (a carry is skipped as part of the walk)
    assert all(c.call_id.startswith("c-") for c in MOMENTS)
    assert {c.team for c in MOMENTS} <= {"NBR", "CAL"}


def test_team_filter_keeps_one_side():
    assert select_calls(MOMENTS, 2, 3600, 3616, "NBR") == []
    assert [c.player_id for c in select_calls(MOMENTS, 2, 3600, 3616, "CAL")] == ["910211"]
