import time

from backend.bank.loader import load_bank
from backend.bank.select import select_for_slot, slot_rng
from backend.bank.types import MatchState, Player

BANK = load_bank()
P8 = Player("p8", "Eight", "AAA", "MF")
P9 = Player("p9", "Nine", "AAA", "MF")
P10 = Player("p10", "Ten", "BBB", "FW")


def _ev(pid, t, typ="pass", outcome=None):
    e = {"id": f"e{pid}{t}", "t": t, "half": 1, "team": "AAA", "type": typ, "player_id": pid}
    if outcome:
        e["outcome"] = outcome
    return e


def _state(t=1200.0, half=1, events=None, match_id="m") -> MatchState:
    return MatchState(
        match_id=match_id, t=t, half=half, home="AAA", away="BBB",
        team_names={"AAA": "A", "BBB": "B"}, score={"AAA": 0, "BBB": 0},
        possession_team="AAA",
        events=events if events is not None else [_ev("p8", 1150), _ev("p9", 1150), _ev("p10", 1150)],
        open_template_ids=[], last_template_id=None, open_window_count=0,
        roster={"p8": P8, "p9": P9, "p10": P10},
    )


USERS = {"u1": P8, "u2": P10}


def test_same_match_and_second_same_decision():
    s = _state()
    assert select_for_slot(s, 1200, USERS, BANK) == select_for_slot(s, 1200, USERS, BANK)
    assert slot_rng("m", 1200).random() == slot_rng("m", 1200.9).random()


def test_kind_draw_roughly_uniform_over_300_slots():
    s = _state()
    counts = {"instant": 0, "window": 0, "player": 0}
    for i in range(300):
        d = select_for_slot(s, 1200 + i * 7, USERS, BANK)
        counts[d.kind] += 1
    for k, n in counts.items():
        assert 0.25 <= n / 300 <= 0.42, counts


def _kind_at(s, users):
    for i in range(200):
        d = select_for_slot(s, 1200 + i, users, BANK)
        if d.kind == "player":
            return 1200 + i
    raise AssertionError("no player slot")


def test_player_slot_falls_back_to_teammate_when_sent_off():
    s = _state(events=[_ev("p8", 1100), _ev("p8", 1150, "card", "red"),
                       _ev("p9", 1150), _ev("p10", 1150)])
    d = None
    for i in range(200):
        d = select_for_slot(s, 1200 + i, USERS, BANK)
        if d.kind == "player":
            break
    assert d.kind == "player"
    assert d.per_user["u1"].template_id == "player_touch_window"
    assert d.per_user["u1"].player_id == "p9"
    assert d.per_user["u2"].player_id == "p10"


def test_player_slot_goes_instant_when_one_user_has_nothing():
    # p8 sent off and no active teammate (p9 silent)
    s = _state(events=[_ev("p8", 1150, "card", "red"), _ev("p10", 1150)])
    for i in range(100):
        d = select_for_slot(s, 1200 + i, USERS, BANK)
        assert d.kind != "player"
        assert d.per_user is None


def test_picker_key_used_when_valid():
    s = _state()
    base = select_for_slot(s, 1200, USERS, BANK)
    kind_slot = next(1200 + i for i in range(200)
                     if select_for_slot(s, 1200 + i, USERS, BANK).kind == "instant")
    from backend.bank.candidates import build_candidates
    cands = build_candidates("instant", s, BANK, kind_slot)
    want = cands[-1].key
    d = select_for_slot(s, kind_slot, USERS, BANK, picker=lambda c, st: want)
    assert d.shared.key == want
    assert base is not None


def test_picker_unknown_key_raises_or_hangs_falls_back():
    s = _state()
    slot = next(1200 + i for i in range(200)
                if select_for_slot(s, 1200 + i, USERS, BANK).kind != "player")

    def boom(c, st):
        raise RuntimeError("x")

    expected = select_for_slot(s, slot, USERS, BANK).shared.key
    for bad in (lambda c, st: "nope", boom, lambda c, st: time.sleep(5)):
        t0 = time.monotonic()
        d = select_for_slot(s, slot, USERS, BANK, picker=bad, timeout_s=0.2)
        assert d.shared.key == expected
        assert time.monotonic() - t0 < 1.0


def test_late_match_window_draw_falls_back_to_instant():
    s = _state(t=4560.0, half=2, events=[])
    bank = {k: v for k, v in BANK.items() if v.kind != "window"}  # no window passes late on
    kinds = {select_for_slot(s, 4560 + i, {}, bank).kind for i in range(60)}
    assert kinds == {"instant"}
