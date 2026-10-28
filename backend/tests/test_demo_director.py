"""test_demo_director.py — the decision log (explain_selection) and GET /demo/director.

The director view must never disagree with the phone app: its picked moments are exactly the
timeline's calls, and every blocked moment names the picked call it lost to.
"""

from __future__ import annotations

import asyncio
import re

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.demo.calls import (MIN_GAP_S, MIN_RESULT_S, MIN_WINDOW_S, Call, explain_selection,
                                find_moments, select_calls)
from backend.demo.director import build_director
from backend.demo.names import TEAMS, clock_label, display_name
from backend.demo.router import make_demo_router
from backend.demo.script import SCRIPTS, Play, build_timeline
from backend.ingest.loader import load_match

MATCH = load_match("nbr-cal-seed42")
SIDES = {"NBR": "NBR", "CAL": "CAL"}
DIR = {team: build_director(MATCH, team=team) for team in SIDES}
TLS = {team: build_timeline(MATCH, team=team) for team in SIDES}

TOO_CLOSE = re.compile(r"Too close to (.+)'s call \((\d+\.\d) s apart, needs 6 s\)")
COVERS = re.compile(r"Would cover (.+)'s result \(needs 3 s after it locks\)")
TOO_SHORT = re.compile(r"Only (\d+\.\d) s to answer \(needs 3 s\)")


async def instant(_: float) -> None:
    await asyncio.sleep(0)


def _call(cid: str, open_t: float, lock_t: float, answer: str = "PASS", team: str = "NBR",
          player: str = "910109") -> Call:
    return Call(cid, 1, open_t, lock_t, player, team, answer)


# --- explain_selection ------------------------------------------------------------


def test_explain_selection_statuses_and_reasons():
    moments = [
        _call("a", 100.0, 104.0, "DRIBBLE", player="910109"),  # Mensah, picked first (rarest)
        _call("b", 102.0, 106.0, "PASS"),  # opens 2.0 s after a
        _call("c", 106.5, 110.0, "PASS"),  # 6.5 s gap but opens 2.5 s after a locks
        _call("d", 120.0, 124.0, "SHOOT", team="CAL"),  # other side
        _call("e", 130.0, 134.0, "PASS"),  # free
        _call("f", 100.0, 200.0, "PASS"),  # locks after the range: not listed
    ]
    out = {d.call.call_id: d for d in explain_selection(moments, 1, 90, 150, "NBR")}
    assert set(out) == {"a", "b", "c", "d", "e"}
    assert (out["a"].status, out["a"].reason, out["a"].against) == ("picked", "Picked", None)
    assert (out["b"].status, out["b"].against) == ("blocked", "a")
    assert out["b"].reason == "Too close to Mensah's call (2.0 s apart, needs 6 s)"
    assert (out["c"].status, out["c"].against) == ("blocked", "a")
    assert out["c"].reason == "Would cover Mensah's result (needs 3 s after it locks)"
    assert (out["d"].status, out["d"].reason, out["d"].against) == (
        "other_team", "Not the fan's team", None)
    assert out["e"].status == "picked"


def test_explain_selection_no_team_lists_both_sides():
    moments = [_call("a", 100.0, 104.0), _call("d", 120.0, 124.0, team="CAL")]
    assert [d.status for d in explain_selection(moments, 1, 90, 150, None)] == ["picked"] * 2


@pytest.mark.parametrize("team", [None, "NBR", "CAL"])
def test_select_calls_is_the_picked_log(team):
    moments = find_moments()
    for script in SCRIPTS.values():
        for p in (s for s in script if isinstance(s, Play)):
            picked = [d.call for d in explain_selection(moments, p.half, p.start_t, p.end_t, team)
                      if d.status == "picked"]
            assert select_calls(moments, p.half, p.start_t, p.end_t, team) == sorted(
                picked, key=lambda c: c.open_t)


def test_find_moments_min_window():
    longer = find_moments()
    every = find_moments(min_window=0)
    assert {c.call_id for c in longer} < {c.call_id for c in every}
    assert [c for c in every if c.lock_t - c.open_t >= MIN_WINDOW_S] == longer


# --- the director JSON ---------------------------------------------------------------


@pytest.mark.parametrize("team", SIDES)
def test_shape(team):
    d = DIR[team]
    assert set(d) == {"team", "teams", "duration", "plays", "cues", "moments", "lines",
                      "movement", "attack", "rules"}
    assert d["team"] == team and d["duration"] == TLS[team].duration
    assert d["teams"] == {"home": TEAMS["NBR"], "away": TEAMS["CAL"]}
    assert d["rules"] == {"min_window_s": MIN_WINDOW_S, "min_gap_s": MIN_GAP_S,
                          "min_result_s": MIN_RESULT_S, "priority": ["DRIBBLE", "SHOOT", "PASS"]}
    plays = [s for s in SCRIPTS[team] if isinstance(s, Play)]
    assert [(p["title"], p["half"], p["start_t"], p["end_t"]) for p in d["plays"]] == [
        (p.title, p.half, p.start_t, p.end_t) for p in plays]
    for p in d["plays"]:
        assert set(p) == {"title", "half", "start_t", "end_t", "at_start", "at_end"}
        assert p["at_end"] - p["at_start"] == pytest.approx(p["end_t"] - p["start_t"])
    assert d["cues"] == [{"at": c.at, "type": c.type, "data": c.data} for c in TLS[team].cues]
    for m in d["moments"]:
        assert set(m) == {"call_id", "half", "open_t", "lock_t", "at_open", "at_lock",
                          "clock_open", "clock_lock", "player", "team", "answer", "status",
                          "reason", "against"}
        assert m["status"] in {"picked", "blocked", "too_short", "other_team"}
        assert m["team"] in {"NBR", "CAL"} and m["answer"] in {"PASS", "SHOOT", "DRIBBLE"}
        assert m["clock_open"] == clock_label(m["half"], m["open_t"])
        assert m["clock_lock"] == clock_label(m["half"], m["lock_t"])
    assert [m["at_open"] for m in d["moments"]] == sorted(m["at_open"] for m in d["moments"])
    assert d["movement"] and all(set(x) == {"at", "v"} and x["v"] >= 0 for x in d["movement"])
    assert d["attack"] and all(set(a) == {"at_start", "at_end", "team"} for a in d["attack"])
    assert all(a["team"] in {"NBR", "CAL"} and a["at_end"] > a["at_start"] for a in d["attack"])


@pytest.mark.parametrize("team", SIDES)
def test_picked_are_the_timeline_calls(team):
    picked = [m for m in DIR[team]["moments"] if m["status"] == "picked"]
    calls = TLS[team].calls
    assert {m["call_id"] for m in picked} == set(calls)
    locks = {c.data["call_id"]: c.at for c in TLS[team].cues if c.type == "call.lock"}
    for m in picked:
        c = calls[m["call_id"]]
        assert (m["half"], m["open_t"], m["lock_t"], m["answer"], m["reason"], m["against"]) == (
            c.half, c.open_t, c.lock_t, c.answer, "Picked", None)
        assert m["player"] == display_name(c.player_id) and m["team"] == team
        assert m["at_lock"] == pytest.approx(locks[c.call_id])  # same mapping as the timeline
        assert m["at_lock"] - m["at_open"] == pytest.approx(c.lock_t - c.open_t)
    assert set(DIR[team]["lines"]) == set(calls)
    for cid, ln in DIR[team]["lines"].items():
        src = TLS[team].lines[cid]
        assert ln == {"facts": src.facts, "setup": src.setup, "explain": src.explain, "ai": src.ai}


@pytest.mark.parametrize("team", SIDES)
def test_blocked_names_a_picked_call(team):
    moments = DIR[team]["moments"]
    by_id = {m["call_id"]: m for m in moments}
    blocked = [m for m in moments if m["status"] == "blocked"]
    assert blocked
    for m in blocked:
        a = by_id[m["against"]]
        assert a["status"] == "picked" and (a["half"], a["team"]) == (m["half"], team)
        if hit := TOO_CLOSE.fullmatch(m["reason"]):
            assert hit[1] == a["player"]
            assert hit[2] == f"{abs(m['open_t'] - a['open_t']):.1f}"
            assert abs(m["open_t"] - a["open_t"]) < MIN_GAP_S
        else:
            hit = COVERS.fullmatch(m["reason"])
            assert hit and hit[1] == a["player"]


@pytest.mark.parametrize("team", SIDES)
def test_too_short_and_other_team(team):
    moments = DIR[team]["moments"]
    short = [m for m in moments if m["status"] == "too_short"]
    assert short or team == "NBR"  # NBR's cut has no short Northbridge moment
    for m in short:
        assert m["team"] == team  # the other side is other_team first, whatever its window
        w = m["lock_t"] - m["open_t"]
        assert w < MIN_WINDOW_S and m["against"] is None
        assert m["reason"] == f"Only {w:.1f} s to answer (needs 3 s)"
        assert TOO_SHORT.fullmatch(m["reason"])
    for m in moments:
        if m["status"] != "too_short" and m["team"] == team:
            assert m["lock_t"] - m["open_t"] >= MIN_WINDOW_S
        if m["status"] == "other_team":
            assert m["team"] != team and m["reason"] == "Not the fan's team"
        elif m["status"] in {"picked", "blocked"}:
            assert m["team"] == team


def test_other_side_short_moment_is_other_team():
    # Doyle, 60:10, 2.5 s window: an Northbridge fan sees him as the other side, not too short
    jp = [m for m in DIR["NBR"]["moments"] if m["clock_open"] == "60:10"]
    assert [(m["player"], m["status"]) for m in jp] == [("Doyle", "other_team")]
    assert jp[0]["lock_t"] - jp[0]["open_t"] < MIN_WINDOW_S


@pytest.mark.parametrize("team", SIDES)
def test_moments_only_inside_plays(team):
    plays = DIR[team]["plays"]
    for m in DIR[team]["moments"]:
        assert any(p["half"] == m["half"] and p["start_t"] <= m["open_t"]
                   and m["lock_t"] <= p["end_t"]
                   and m["at_open"] == pytest.approx(p["at_start"] + m["open_t"] - p["start_t"])
                   for p in plays)
    every = find_moments(min_window=0)
    inside = {c.call_id for c in every for p in plays
              if c.half == p["half"] and p["start_t"] <= c.open_t and c.lock_t <= p["end_t"]}
    assert {m["call_id"] for m in DIR[team]["moments"]} == inside


@pytest.mark.parametrize("team", SIDES)
def test_movement_and_attack_inside_plays(team):
    plays = DIR[team]["plays"]

    def inside(a: float, b: float) -> bool:
        return any(p["at_start"] - 1e-9 <= a and b <= p["at_end"] + 1e-9 for p in plays)
    assert all(inside(x["at"], x["at"]) for x in DIR[team]["movement"])
    assert all(inside(a["at_start"], a["at_end"]) for a in DIR[team]["attack"])
    # one sample per real second of play
    assert len(DIR[team]["movement"]) == sum(
        int(-(-(p["end_t"] - p["start_t"]) // 1)) for p in plays)


# --- route -----------------------------------------------------------------------------


def _client(load=lambda: MATCH):
    app = FastAPI()
    router = make_demo_router(sleep=instant, load=load)
    app.include_router(router)
    return TestClient(app)


def test_route_default_team_and_cache():
    loads = []

    def load():
        loads.append(1)
        return MATCH
    with _client(load) as client:
        r = client.get("/demo/director")
        assert r.status_code == 200 and r.json()["team"] == "NBR"
        assert client.get("/demo/director?team=NBR").json() == r.json()
        che = client.get("/demo/director", params={"team": "CAL"})
        assert che.status_code == 200 and che.json()["team"] == "CAL"
        client.get("/demo/director?team=CAL")
    assert len(loads) == 2  # built once per side


@pytest.mark.parametrize("bad", ["XYZ", "nbr", "", "ARS"])
def test_route_unknown_team_is_400(bad):
    with _client() as client:
        assert client.get("/demo/director", params={"team": bad}).status_code == 400
