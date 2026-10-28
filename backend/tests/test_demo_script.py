from backend.ingest.loader import load_match
from backend.demo.script import DRIBBLE_LEAD, MIN_OPEN_S, build_timeline

TL = build_timeline(load_match("nbr-cal-seed42"))
types = [c.type for c in TL.cues]

def test_about_ninety_seconds():
    assert TL.duration == 99.0

def test_calls_and_mix():
    assert len(TL.calls) == 6
    answers = [c.answer for c in TL.calls.values()]
    assert answers == ["PASS", "DRIBBLE", "SHOOT", "SHOOT", "DRIBBLE", "SHOOT"]
    assert all(c.team == "NBR" for c in TL.calls.values())  # an Northbridge fan is asked about Northbridge
    assert [c.player_id for c in TL.calls.values()][:3] == ["910104", "910106", "910108"]

def test_goals_and_final_score():
    goals = [c.data for c in TL.cues if c.type == "demo.goal"]
    assert [g["player"] for g in goals] == ["Mensah", "Doyle", "Hale"]
    assert goals[-1]["score"] == [2, 1]
    assert [c for c in TL.cues if c.type == "demo.full_time"][0].data["score"] == [2, 1]

def test_every_call_opens_before_it_locks():
    opens = {c.data["call_id"]: c.at for c in TL.cues if c.type == "call.open"}
    locks = {c.data["call_id"]: c.at for c in TL.cues if c.type == "call.lock"}
    assert opens.keys() == locks.keys() == TL.calls.keys()
    assert all(MIN_OPEN_S - 1e-9 <= locks[k] - opens[k] <= 8.0 for k in opens)

def test_cues_sorted_and_jumps():
    assert [c.at for c in TL.cues] == sorted(c.at for c in TL.cues)
    assert types.count("demo.jump") == 6 and types[-1] == "demo.full_time"
    jumps = [c for c in TL.cues if c.type == "demo.jump"]
    assert jumps[0].at == 0 and TL.cues[0] is jumps[0]
    assert jumps[0].data == {"title": "Northbridge on the attack", "clock": "11:34"}
    assert [j.data["title"] for j in jumps] == [
        "Northbridge on the attack", "Northbridge keep pushing", "Northbridge in the box",
        "Calder on the break", "Northbridge go again", "Late pressure"]

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
        (2, 4424.456, 4428.951, "910119"), (2, 4786.224, 4789.933, "910105")]


def test_chelsea_fan_gets_chelsea_calls():
    che = build_timeline(load_match("nbr-cal-seed42"), team="CAL")
    assert len(che.calls) == 6 and all(c.team == "CAL" for c in che.calls.values())
    assert sorted({c.answer for c in che.calls.values()}) == ["DRIBBLE", "PASS", "SHOOT"]
    goals = [c.data["player"] for c in che.cues if c.type == "demo.goal"]
    assert goals == ["Mensah", "Doyle", "Hale"]  # every goal of the match is still shown
    assert 90 <= che.duration <= 105 and che.team == "CAL"


def test_dribble_is_visible_at_its_lock():
    dribble = next(c for c in TL.calls.values() if c.answer == "DRIBBLE")
    lock = next(c for c in TL.cues if c.type == "call.lock" and c.data["call_id"] == dribble.call_id)
    events = [c for c in TL.cues if c.type == "demo.event" and c.data["type"] == "dribble"
              and c.data["player"] == "Pryce"]
    assert len(events) == 1 and events[0].at == lock.at
    ev = events[0].data
    assert ev["team"] == "NBR" and ev["player"] == "Pryce" and ev["end_xy"] is None
    x, y = ev["xy"]
    assert 0 <= x <= 105 and 0 <= y <= 68
    # the take-on starts DRIBBLE_LEAD before the lock: he runs at a defender standing on the
    # dribble spot, steps round him as he bites, and is past him at the lock; then on to his pass
    start = round(lock.at - DRIBBLE_LEAD, 3)
    step = next(c for c in TL.cues if c.type == "demo.shape" and round(c.at, 3) == start)
    burst = next(c for c in TL.cues if c.type == "demo.shape" and c.at > step.at + 1e-6)
    after = next(c for c in TL.cues if c.type == "demo.shape" and c.at == lock.at)
    before = [c for c in TL.cues if c.type == "demo.shape" and c.at < step.at - 1e-6][-1]
    assert step.data["holder"] == burst.data["holder"] == "Pryce"
    assert round(step.data["dur"] + burst.data["dur"], 3) == DRIBBLE_LEAD
    them = lambda c: {p["num"]: (p["x"], p["y"]) for p in c.data["players"] if p["team"] == "CAL"}
    me = lambda c: next((p["x"], p["y"]) for p in c.data["players"]
                        if p["team"] == "NBR" and p["num"] == 36)
    dist = lambda a, b: ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5
    assert me(before)[0] < ev["xy"][0]  # runs at his man from behind the spot
    defender = min(them(before), key=lambda n: dist(them(before)[n], ev["xy"]))
    assert dist(them(before)[defender], ev["xy"]) < 1.0  # standing on the spot
    assert dist(me(step), them(step)[defender]) > 4.2  # a dot is ~4 m wide: no overlap
    assert dist(me(burst), them(burst)[defender]) > 4.2 and me(burst)[0] > them(burst)[defender][0]
    nxt = next(c for c in TL.cues if c.type == "demo.event" and c.at > lock.at)
    assert list(me(after)) == nxt.data["xy"] and after.data["dur"] == events[0].data["dur"]
    assert me(burst)[0] <= me(after)[0]  # always moving forward
    assert them(after)[defender] == them(burst)[defender]  # beaten, he stays where he lunged


def test_motion_lands_on_the_next_beat():
    # every located event says how long until the next one, and its shape gets there in that time
    evs = [c for c in TL.cues if c.type == "demo.event" and c.data["xy"] and c.data["type"] != "goal"]
    for e in evs:
        shape = [c for c in TL.cues if c.type == "demo.shape" and abs(c.at - e.at) < 1e-6][-1]
        assert shape.data["dur"] == e.data["dur"] and 0 <= e.data["flight"] <= e.data["dur"]
    passes = [e.data for e in evs if e.data["type"] == "pass" and e.data["to"]]
    assert ("Fairbanks", "Pryce") in [(p["player"], p["to"]) for p in passes]
    long_ball = next(p for p in passes if p["player"] == "Fairbanks" and p["to"] == "Pryce")
    assert 3.0 < long_ball["flight"] < long_ball["dur"]  # about 18 m/s over 59 m


def test_each_clip_starts_with_a_placed_shape():
    jumps = [i for i, c in enumerate(TL.cues) if c.type == "demo.jump"]
    for i in jumps:
        shape = next(c for c in TL.cues[i:] if c.type == "demo.shape")
        assert shape.data["dur"] == 0


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
