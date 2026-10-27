from backend.ingest.loader import load_match
from backend.demo.script import build_timeline

TL = build_timeline(load_match("nbr-cal-seed42"))
types = [c.type for c in TL.cues]

def test_about_ninety_seconds():
    assert TL.duration == 82.0

def test_calls_and_mix():
    assert len(TL.calls) == 6
    answers = [c.answer for c in TL.calls.values()]
    assert answers == ["PASS", "DRIBBLE", "SHOOT", "SHOOT", "PASS", "SHOOT"]

def test_goals_and_final_score():
    goals = [c.data for c in TL.cues if c.type == "demo.goal"]
    assert [g["player"] for g in goals] == ["Saka", "João Pedro", "Saliba"]
    assert goals[-1]["score"] == [2, 1]
    assert [c for c in TL.cues if c.type == "demo.full_time"][0].data["score"] == [2, 1]

def test_every_call_opens_before_it_locks():
    opens = {c.data["call_id"]: c.at for c in TL.cues if c.type == "call.open"}
    locks = {c.data["call_id"]: c.at for c in TL.cues if c.type == "call.lock"}
    assert opens.keys() == locks.keys() == TL.calls.keys()
    assert all(3.0 <= locks[k] - opens[k] <= 8.0 for k in opens)

def test_cues_sorted_and_jumps():
    assert [c.at for c in TL.cues] == sorted(c.at for c in TL.cues)
    assert types.count("demo.jump") == 3 and types[-1] == "demo.full_time"

def test_windows_do_not_overlap():
    opens = [c for c in TL.cues if c.type == "call.open"]
    locks = [c for c in TL.cues if c.type == "call.lock"]
    assert len(opens) == len(locks) == 6
    for prev_lock, nxt in zip(locks, opens[1:]):
        assert nxt.at >= prev_lock.at
