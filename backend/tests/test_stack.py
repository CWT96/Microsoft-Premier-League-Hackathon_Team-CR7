"""Kickoff and half-time stacks: shared window cards plus one player card per user."""
import random

from backend.bank.loader import load_bank
from backend.bank.settle import settle_candidate
from backend.bank.stack import restart_state, select_stack
from backend.bank.types import MatchState, Player

BANK = load_bank()
GK = Player("a1", "Ward", "AAA", "GK")
MF = Player("a8", "Eight", "AAA", "MF")
MF2 = Player("a9", "Nine", "AAA", "MF")
FW = Player("b9", "Striker", "BBB", "FW")
ROSTER = {p.player_id: p for p in (GK, MF, MF2, FW)}
USERS = {"u1": GK, "u2": FW}


def _ev(n, t, half, team, typ, pid=None, outcome=None):
    e = {"id": f"e{n}", "t": t, "half": half, "team": team, "type": typ}
    if pid:
        e["player_id"] = pid
    if outcome:
        e["outcome"] = outcome
    return e


def _state(t=0.0, half=1, events=None, score=None, match_id="m", **kw) -> MatchState:
    base = dict(
        match_id=match_id, t=t, half=half, home="AAA", away="BBB",
        team_names={"AAA": "A", "BBB": "B"}, score=score or {"AAA": 0, "BBB": 0},
        possession_team="AAA", events=events or [], open_template_ids=[],
        last_template_id=None, open_window_count=0, roster=ROSTER)
    base.update(kw)
    return MatchState(**base)


def test_kickoff_stack_shape():
    d = select_stack(_state(), "kickoff", USERS, BANK)
    assert d.phase == "kickoff" and d.open_at == 0.0
    ids = [c.template_id for c in d.shared]
    assert len(d.shared) == 2 and len(set(ids)) == 2
    for c in d.shared:
        assert c.kind == "window"
        assert c.window.start_t == 0.0
        secs = {"5m": 300, "10m": 600, "20m": 1200}.get(c.params.get("window"),
                                                         c.params.get("window_s"))
        if secs is not None:
            assert c.window.end_t == secs
        assert "the next" not in c.text
    assert set(d.per_user) == {"u1", "u2"}
    assert d.per_user["u1"].player_id == "a1" and d.per_user["u2"].player_id == "b9"
    for c in d.per_user.values():
        assert "the next" not in c.text
        assert c.window.start_t == 0.0


def test_kickoff_text_first_n_minutes():
    texts = []
    for i in range(30):
        d = select_stack(_state(match_id=f"x{i}"), "kickoff", USERS, BANK)
        texts += [c.text for c in d.shared + list(d.per_user.values())]
    assert any("the first " in t for t in texts)
    assert not any("the next" in t for t in texts)


def test_deterministic_and_varies_by_match():
    a = select_stack(_state(), "kickoff", USERS, BANK)
    assert a == select_stack(_state(), "kickoff", USERS, BANK)
    others = [select_stack(_state(match_id=f"id{i}"), "kickoff", USERS, BANK) for i in range(6)]
    assert any(o != a for o in others)


def _half_time_state(**kw):
    events = [_ev(1, 300.0, 1, "AAA", "goal", "a8"), _ev(2, 900.0, 1, "BBB", "goal", "b9"),
              _ev(3, 2000.0, 1, "AAA", "pass", "a8", "complete"),
              _ev(4, 2750.0, 1, "AAA", "goal", "a8"),  # first-half added time
              _ev(5, 2760.0, 1, "BBB", "shot", "b9", "off_target"),
              {"id": "he", "t": 2790.0, "half": 1, "type": "half_end"}]
    return _state(t=2800.0, half=1, events=events, score={"AAA": 2, "BBB": 1}, **kw)


def test_half_time_stack():
    seen_second_half = False
    for i in range(40):
        d = select_stack(_half_time_state(match_id=f"h{i}"), "half_time", USERS, BANK)
        assert d.open_at == 2700.0
        for c in d.shared + list(d.per_user.values()):
            assert c.template_id != "both_teams_score"
            if BANK[c.template_id].counts_from == "kickoff":
                assert c.window.start_t == 0.0 and c.window.half is None
                continue
            assert c.window.start_t == 2700.0
            # whole-match windows have no half filter; the rest count the second half only
            assert c.window.half == (None if c.window.end_marker == "full_end" else 2)
            if "{window_label}" in BANK[c.template_id].text and (
                    c.params.get("window") in ("5m", "10m", "20m") or "window_s" in c.params):
                assert c.text.count("of the second half") == 1
                assert "the next" not in c.text
                seen_second_half = True
    assert seen_second_half


def test_open_window_count_does_not_block_shared():
    s = _state(open_window_count=2, open_template_ids=["goal_window"], last_template_id="x")
    d = select_stack(s, "kickoff", USERS, BANK)
    assert len(d.shared) == 2


def _red(pid):
    return _ev(9, 100.0, 1, "AAA", "card", pid, "red")


def test_sent_off_player_falls_back_to_teammate():
    s = _state(t=0.0, events=[_red("a1")], roster={**ROSTER, "a2": Player("a2", "Bk", "AAA", "GK")})
    d = select_stack(s, "kickoff", USERS, BANK)
    c = d.per_user["u1"]
    assert c.player_id == "a2" and c.template_id == "player_touch_window"


def test_sent_off_player_without_teammate_means_no_player_card():
    d = select_stack(_state(events=[_red("a1")]), "kickoff", USERS, BANK)
    assert d.per_user is None
    assert len(d.shared) == 2


def test_empty_stack_is_none():
    s = _state(events=[_red("a1")])
    assert select_stack(s, "kickoff", {"u1": GK}, {}) is None


def test_stack_candidates_settle():
    rng = random.Random(1)
    for phase, mk, half, lo in (("kickoff", _state, 1, 1), ("half_time", _half_time_state, 2, 2701)):
        for i in range(20):
            d = select_stack(mk(match_id=f"s{i}"), phase, USERS, BANK)
            later = [_ev(100 + n, float(rng.randint(lo, lo + 2000)), half,
                         rng.choice(["AAA", "BBB"]), rng.choice(["pass", "shot", "save", "foul"]),
                         rng.choice(list(ROSTER)), rng.choice(["complete", "on_target"]))
                     for n in range(30)]
            later.append({"id": "he2", "t": 5400.0, "half": 2, "type": "full_end"})
            later.append({"id": "he3", "t": 5400.0, "half": 2, "type": "half_end"})
            for c in d.shared:
                r = settle_candidate(c, later, BANK, closed=True)
                assert r is not None and (r == "void" or r in c.options)
            for c in d.per_user.values():
                assert settle_candidate(c, later, BANK, closed=True) in c.options


def test_half_time_added_time_events_do_not_settle_stack():
    st = _half_time_state()
    for i in range(40):
        d = select_stack(_half_time_state(match_id=f"a{i}"), "half_time", USERS, BANK)
        for c in d.shared + list(d.per_user.values()):
            if BANK[c.template_id].counts_from == "kickoff":
                continue
            assert settle_candidate(c, st.events, BANK, closed=False) is None, c.template_id


def test_half_label_in_stacks():
    from backend.bank.loader import window_label
    assert window_label("half", "half_time") == "the second half"
    assert window_label("half", "kickoff") == "the first half"
    assert window_label("half") == "the rest of this half"
    assert window_label("match", "half_time") == "this match"


def test_restart_state():
    s = restart_state(_half_time_state(open_window_count=2), "half_time")
    assert (s.t, s.half, s.possession_team, s.open_window_count) == (2700.0, 2, None, 0)
    assert s.open_template_ids == [] and s.last_template_id is None
    assert s.score == {"AAA": 2, "BBB": 1} and len(s.events) == 6
