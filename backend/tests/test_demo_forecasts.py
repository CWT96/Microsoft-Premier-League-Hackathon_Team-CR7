from backend.ingest.loader import load_match
from backend.demo.forecasts import WINDOWS, window, outcome, score_window, score_final, result_of

M = load_match("nbr-cal-seed42")
F5, F10, F15 = WINDOWS

def test_defs():
    assert [(d.key, d.minutes, d.points) for d in WINDOWS] == [("f5",5,500),("f10",10,1000),("f15",15,1500)]
    assert [o for o, _ in F10.options] == ["ars", "che"]
    assert (F5.label, F5.question, F5.options) == (
        "NEXT 5 MIN", "Who has more of the ball?", (("ars", "ARSENAL"), ("che", "CHELSEA")))

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
    assert score_window(F5, "ars", "ars") == 500 and score_window(F5, "che", "ars") == 0
    assert score_window(F5, "ars", "none") == 0
    assert result_of(2, 1) == "ars" and result_of(1, 1) == "draw"
    assert score_final("ars", 2, 1, (2, 1)) == (3000, 2000)
    assert score_final("ars", 3, 1, (2, 1)) == (3000, 0)
    assert score_final("draw", 1, 1, (2, 1)) == (0, 0)


def test_f5_more_passes_wins():
    from backend.core.types import MatchEvent
    def ev(team, t, kind="pass"):
        return MatchEvent(id=f"{team}{t}", t=t, half=1, type=kind, team=team)
    evs = [ev("NBR", 10), ev("NBR", 20), ev("CAL", 30), ev("CAL", 40, "shot")]
    assert outcome(evs, "f5", 1, 0, 300) == "ars"
    assert outcome(evs + [ev("CAL", 50)], "f5", 1, 0, 300) == "none"  # even split
    assert outcome(evs + [ev("CAL", 50), ev("CAL", 60)], "f5", 1, 0, 300) == "che"
    assert outcome(evs, "f5", 2, 0, 300) == "none"


def test_f5_outcomes_vary():
    """Not free points: over every pickable clock in the cut, both sides win the f5."""
    from backend.demo.script import build_timeline
    tl = build_timeline(M)
    got = []
    for c in tl.cues:
        if c.type == "demo.clock":
            h, t0 = c.data["half"], c.data["t"]
            got.append(outcome(M.events, "f5", h, *window(F5, h, t0, tl.half_ends[h])))
    assert "ars" in got and "che" in got
