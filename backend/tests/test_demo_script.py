from backend.ingest.loader import load_match
from backend.demo.script import build_timeline

TL = build_timeline(load_match("nbr-cal-seed42"))
types = [c.type for c in TL.cues]

def test_about_ninety_seconds():
    assert TL.duration == 93.0

def test_calls_and_mix():
    assert len(TL.calls) == 6
    answers = [c.answer for c in TL.calls.values()]
    assert answers == ["PASS", "DRIBBLE", "SHOOT", "SHOOT", "PASS", "SHOOT"]
    assert [c.player_id for c in TL.calls.values()][:3] == ["910104", "910106", "910108"]

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
    assert types.count("demo.jump") == 5 and types[-1] == "demo.full_time"
    jumps = [c for c in TL.cues if c.type == "demo.jump"]
    assert jumps[0].at == 0 and TL.cues[0] is jumps[0]
    assert jumps[0].data == {"title": "Arsenal on the attack", "clock": "11:34"}
    assert [j.data["title"] for j in jumps] == [
        "Arsenal on the attack", "Arsenal keep pushing", "Saka's goal", "Chelsea hit back", "The winner"]

def test_windows_do_not_overlap():
    opens = [c for c in TL.cues if c.type == "call.open"]
    locks = [c for c in TL.cues if c.type == "call.lock"]
    assert len(opens) == len(locks) == 6
    for prev_lock, nxt in zip(locks, opens[1:]):
        assert nxt.at >= prev_lock.at


def test_calls_unchanged_by_the_cut():
    assert [(c.half, round(c.open_t, 3), round(c.lock_t, 3), c.player_id)
            for c in TL.calls.values()] == [
        (1, 696.555, 699.671, "910104"), (1, 703.418, 711.336, "910106"),
        (1, 1012.86, 1018.49, "910108"), (1, 1429.976, 1434.263, "910109"),
        (2, 3605.987, 3610.652, "910211"), (2, 4786.224, 4789.933, "910105")]


def test_dribble_is_visible_at_its_lock():
    dribble = next(c for c in TL.calls.values() if c.answer == "DRIBBLE")
    lock = next(c for c in TL.cues if c.type == "call.lock" and c.data["call_id"] == dribble.call_id)
    events = [c for c in TL.cues if c.type == "demo.event" and c.data["type"] == "dribble"]
    assert len(events) == 1 and events[0].at == lock.at
    ev = events[0].data
    assert ev["team"] == "ARS" and ev["player"] == "Zubimendi" and ev["end_xy"] is None
    x, y = ev["xy"]
    assert 0 <= x <= 105 and 0 <= y <= 68
    shape = next(c for c in TL.cues if c.type == "demo.shape" and c.at == lock.at
                 and TL.cues.index(c) > TL.cues.index(events[0]))
    holder = [p for p in shape.data["players"] if p["team"] == "ARS" and p["num"] == 36]
    assert len(holder) == 1 and [holder[0]["x"], holder[0]["y"]] == ev["xy"]


def test_result_stays_on_screen_before_the_next_call():
    # each call's result shows >= 3 s of play before the next call opens (no overlap)
    opens = [c.at for c in TL.cues if c.type == "call.open"]
    locks = [c.at for c in TL.cues if c.type == "call.lock"]
    for prev_lock, nxt in zip(locks, opens[1:]):
        assert nxt - prev_lock >= 3.0


def test_goal_result_pop_not_cut_by_the_break():
    # a goal that settles a call leaves >= 5 s of play before the next jump / half / full time
    breaks = [c.at for c in TL.cues if c.type in ("demo.jump", "demo.half_time", "demo.full_time")]
    locks = {c.at for c in TL.cues if c.type == "call.lock"}
    goals = [c.at for c in TL.cues if c.type == "demo.goal" and c.at in locks]
    assert len(goals) == 2
    for at in goals:
        assert min(b for b in breaks if b > at) - at >= 5.0
