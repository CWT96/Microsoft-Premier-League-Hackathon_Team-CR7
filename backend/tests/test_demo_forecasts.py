from backend.ingest.loader import load_match
from backend.demo.forecasts import WINDOWS, window, outcome, score_window, score_final, result_of

M = load_match("nbr-cal-seed42")
F5, F10, F15 = WINDOWS

def test_defs():
    assert [(d.key, d.minutes, d.points) for d in WINDOWS] == [("f5",5,500),("f10",10,1000),("f15",15,1500)]
    assert [o for o, _ in F10.options] == ["ars", "che"]

def test_window_clamped_to_half():
    assert window(F15, 1, 2500.0, M.first_half_end_t) == (2500.0, M.first_half_end_t)
    assert window(F5, 1, 100.0, M.first_half_end_t) == (100.0, 400.0)

def test_outcomes_from_events():
    assert outcome(M.events, "f15", 1, 1420.0, 2320.0) == "goal"        # Saka 23:54
    assert outcome(M.events, "f10", 1, 1420.0, 2020.0) == "ars"         # Saka's shot first
    assert outcome(M.events, "f10", 1, 100.0, 700.0) == "che"           # first shot CAL at ~345
    assert outcome(M.events, "f15", 1, 100.0, 1000.0) == "nogoal"
    assert outcome(M.events, "f10", 1, 2850.0, M.first_half_end_t) == "none"

def test_scores():
    assert score_window(F5, "no", "no") == 500 and score_window(F5, "yes", "no") == 0
    assert result_of(2, 1) == "ars" and result_of(1, 1) == "draw"
    assert score_final("ars", 2, 1, (2, 1)) == (3000, 2000)
    assert score_final("ars", 3, 1, (2, 1)) == (3000, 0)
    assert score_final("draw", 1, 1, (2, 1)) == (0, 0)
