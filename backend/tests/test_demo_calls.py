from backend.demo.calls import find_moments, select_calls
from backend.ingest.loader import load_match

M = load_match("nbr-cal-seed42")
MOMENTS = find_moments(M)


def test_moment_mix():
    answers = [c.answer for c in MOMENTS]
    assert len(MOMENTS) == 254
    assert answers.count("SHOOT") == 25 and answers.count("DRIBBLE") == 32
    assert answers.count("PASS") == 197


def test_saka_goal_call_is_shoot():
    calls = select_calls(MOMENTS, 1, 1400, 1440)
    assert [round(c.lock_t) for c in calls] == [1422, 1432]
    assert calls[-1].player_id == "910109" and calls[-1].answer == "SHOOT"


def test_min_gap_respected():
    calls = select_calls(MOMENTS, 2, 3595, 3620)
    assert [round(c.lock_t) for c in calls] == [3608, 3613]
    assert all(b.lock_t - a.lock_t >= 5.0 for a, b in zip(calls, calls[1:]))


def test_empty_range():
    assert select_calls(MOMENTS, 1, 0, 8) == []
