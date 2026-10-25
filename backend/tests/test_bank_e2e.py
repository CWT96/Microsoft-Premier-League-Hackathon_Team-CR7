"""End-to-end: slot selection over a dull spell, then settling every candidate."""
import random
from collections import Counter
from dataclasses import replace

import pytest

from backend.bank.loader import load_bank
from backend.bank.select import select_for_slot
from backend.bank.settle import settle_candidate
from backend.bank.types import MatchState, Player

BANK = load_bank()
HOME_GK = Player("a1", "Home Keeper", "AAA", "GK")
HOME_MF = Player("a8", "Home Eight", "AAA", "MF")
AWAY_GK = Player("b1", "Away Keeper", "BBB", "GK")
AWAY_MF = Player("b8", "Away Eight", "BBB", "MF")
AWAY_FW = Player("b9", "Away Nine", "BBB", "FW")
ROSTER = {p.player_id: p for p in (HOME_GK, HOME_MF, AWAY_GK, AWAY_MF, AWAY_FW)}
USERS = {"u_home": HOME_GK, "u_away": AWAY_FW}
TEAM = {p.player_id: p.team for p in ROSTER.values()}
TYPES = ["pass", "pass", "pass", "carry", "shot", "save", "clearance", "tackle",
         "interception", "foul", "card", "goal"]


def _pass(n, t, pid, start, end):
    return {"id": f"d{n}", "t": float(t), "half": 1, "team": TEAM[pid], "type": "pass",
            "player_id": pid, "outcome": "complete", "start": start, "end": end}


def _dull_spell() -> list[dict]:
    """Two minutes of midfield passing before 20:00, both users' players involved."""
    rng = random.Random(7)
    events, pids = [], ["a8", "a1", "a8", "b8", "b9", "b8", "a8", "a1"]
    for n, t in enumerate(range(1080, 1200, 8)):
        x, y = rng.uniform(40, 60), rng.uniform(15, 53)
        events.append(_pass(n, t, pids[n % len(pids)], [x, y],
                            [x + rng.uniform(-8, 8), y + rng.uniform(-6, 6)]))
    return events


def _state(t: float) -> MatchState:
    return MatchState(
        match_id="e2e", t=t, half=1, home="AAA", away="BBB",
        team_names={"AAA": "Northbridge FC", "BBB": "Calder Town"},
        score={"AAA": 0, "BBB": 0}, possession_team="AAA", events=_dull_spell(),
        open_template_ids=[], last_template_id=None, open_window_count=0, roster=ROSTER,
    )


def _future(rng: random.Random, after: float, n: int) -> list[dict]:
    """Random later events in half 1, sometimes ending with half_end."""
    out, t = [], after
    for i in range(n):
        t += rng.uniform(1, 60)
        if t >= 2700 + 120:
            break
        pid = rng.choice(list(ROSTER))
        typ = rng.choice(TYPES)
        start = [rng.uniform(0, 105), rng.uniform(0, 68)]
        e = {"id": f"f{i}", "t": t, "half": 1, "team": TEAM[pid], "type": typ,
             "player_id": pid, "start": start,
             "end": [min(105, max(0, start[0] + rng.uniform(-20, 20))),
                     min(68, max(0, start[1] + rng.uniform(-30, 30)))]}
        if typ == "pass":
            e["outcome"] = rng.choice(["complete", "complete", "incomplete"])
        elif typ == "shot":
            e["outcome"] = rng.choice(["on_target", "off_target", "saved", "goal"])
        elif typ == "tackle":
            e["outcome"] = rng.choice(["won", "lost"])
        elif typ == "card":
            e["outcome"] = rng.choice(["yellow", "yellow", "red"])
        out.append(e)
    if rng.random() < 0.5:
        out.append({"id": "he1", "t": max(t, 2700.0) + 1, "half": 1, "type": "half_end"})
    return out


OPEN_ATS = [1200 + 6 * i for i in range(51)]  # 1200..1500


def test_every_slot_candidate_is_shown_and_settles_to_a_shown_option():
    rng = random.Random(2026)
    kinds, results = Counter(), Counter()
    for open_at in OPEN_ATS:
        state = _state(float(open_at))
        dec = select_for_slot(state, open_at, USERS, BANK)
        if dec is None:
            continue
        kinds[dec.kind] += 1
        cands = [dec.shared] if dec.shared else list(dec.per_user.values())
        for c in cands:
            assert len(c.options) == 2, c
            assert len(c.text) <= 140, c.text
            assert sum(c.p.values()) == pytest.approx(1.0)
            assert set(c.p) == set(c.options)
            for _ in range(8):
                events = state.events + _future(rng, open_at, rng.randint(0, 40))
                for closed in (False, True):
                    r = settle_candidate(c, events, BANK, closed=closed)
                    results[r] += 1
                    if c.kind == "player":
                        assert r is None or r in c.options, (c.template_id, r)
                    else:
                        assert r is None or r == "void" or r in c.options, (c.template_id, r)
    assert sum(kinds.values()) >= 50
    assert set(kinds) == {"instant", "window", "player"}, kinds
    assert results["void"] > 0 and results[None] > 0
