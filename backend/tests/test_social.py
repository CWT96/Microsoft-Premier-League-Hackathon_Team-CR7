"""test_social.py — tests for backend/social (bots, crowd, funnel, leaderboard)."""

from __future__ import annotations

from types import SimpleNamespace

from backend.social.bots import make_bots
from backend.social.crowd import combine_split, crowd_split, split_for_result
from backend.social.funnel import average_score, ops_funnel
from backend.social.leaderboard import (
    leaderboard,
    simulated_global_entries,
    simulated_global_scores,
)
from backend.tests.contract_utils import validate

PROBS = {"forward": 0.7, "backward": 0.3}
IDS = list(PROBS)


def _q(qid: str) -> SimpleNamespace:
    return SimpleNamespace(qid=qid, options=[SimpleNamespace(id=k) for k in PROBS])


def _bot(prefix: str):
    return next(b for b in make_bots(1) if b.name.startswith(prefix))


def test_bots_deterministic_and_three():
    assert len(make_bots(7)) == 3
    a = [b.decide(_q("q-1"), PROBS) for b in make_bots(7)]
    assert a == [b.decide(_q("q-1"), PROBS) for b in make_bots(7)]


def test_sam_follows_ai_most_of_the_time():
    sam = _bot("Sam")
    picks = [sam.decide(_q(f"q-{i}"), PROBS)[0] for i in range(400)]
    answered = [p for p in picks if p]
    assert answered.count("forward") / len(answered) > 0.75


def test_jo_is_roughly_random():
    jo = _bot("Jo")
    picks = [p for p in (jo.decide(_q(f"q-{i}"), PROBS)[0] for i in range(600)) if p]
    assert 0.4 < picks.count("forward") / len(picks) < 0.6


def test_engagement_below_100_percent_and_latency_in_window():
    for bot in make_bots(3):
        res = [bot.decide(_q(f"q-{i}"), PROBS) for i in range(400)]
        rate = sum(1 for o, _ in res if o) / len(res)
        assert rate < 1.0
        assert all(0 < lat < 6 for _, lat in res)


def test_crowd_split_sums_to_100_and_deterministic():
    s = crowd_split("q-1", PROBS, 5, IDS)
    assert sum(s.values()) == 100
    assert s == crowd_split("q-1", PROBS, 5, IDS)
    assert s["forward"] > s["backward"]


def test_crowd_split_handles_odd_probs():
    s = crowd_split("q-2", {"a": 0.0, "b": 1.0}, 1, ["a", "b"])
    assert sum(s.values()) == 100


def test_combine_split():
    assert combine_split({}, ["a", "b"]) == {}
    assert combine_split({"u1": "a", "u2": "b", "u3": "b"}, ["a", "b"]) == {"a": 33, "b": 67}


def test_split_for_result_only_scopes_with_data_and_valid():
    only_global = split_for_result("q-1", PROBS, 1, IDS)
    assert set(only_global) == {"global"}
    full = split_for_result("q-1", PROBS, 1, IDS, {"u": "forward"}, {"Sam": "forward"})
    assert set(full) == {"global", "room", "friends"}
    result = {
        "qid": "q-1", "status": "resolved", "correct_option": "forward", "message": "x",
        "explanation": None, "new_term": None, "evidence": [], "facts": {},
        "crowd_split": full,
    }
    validate("result", result)


def test_funnel_monotone_and_deterministic():
    for seed in range(20):
        f = ops_funnel("m", seed)
        assert f["scanned"] >= f["answered_5plus"] >= f["signed_up"] >= f["redeemed"] >= 0
        assert f["new_members"] <= f["signed_up"]
        assert f == ops_funnel("m", seed)


def test_average_score():
    assert average_score([]) == 0.0
    assert average_score([400, 600]) == 500.0


def test_leaderboard_top10_plus_you():
    entries = [(f"p{i}", 900 - i, False) for i in range(20)] + [("You", 100, True)]
    lb = leaderboard("room", entries)
    assert lb["scope"] == "room"
    assert len(lb["entries"]) == 11
    assert lb["entries"][-1] == {"display_name": "You", "final_score": 100, "is_you": True}


def test_leaderboard_you_in_top_not_duplicated():
    entries = [("You", 999, True)] + [(f"p{i}", 500 - i, False) for i in range(15)]
    lb = leaderboard("friends", entries)
    assert len(lb["entries"]) == 10
    assert lb["entries"][0]["is_you"]


def test_simulated_global_scores():
    s = simulated_global_scores(3000, 1)
    assert s == simulated_global_scores(3000, 1)
    assert len(s) == 3000 and all(0 <= x <= 1000 for x in s)
    assert 500 < sum(s) / len(s) < 700


def test_combine_split_zero_fills_and_returns_ints():
    s = combine_split({"u": "a"}, ["a", "b"])
    assert s == {"a": 100, "b": 0}
    assert all(isinstance(v, int) for v in s.values())


def test_leaderboard_you_wins_ties():
    lb = leaderboard("global", [("Ann", 500, False), ("You", 500, True)], top=1)
    assert lb["entries"][0]["is_you"]


def test_simulated_global_entries():
    e = simulated_global_entries(500, 2)
    assert e == simulated_global_entries(500, 2)
    assert len({n for n, _ in e}) == 500
    assert [s for _, s in e] == simulated_global_scores(500, 2)
