"""test_demo_bonus.py — backed players: squads, per-position stats from raw events, the bonus."""

from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.demo import bonus
from backend.demo.router import make_demo_router
from backend.demo.script import build_timeline
from backend.demo.session import DemoSession
from backend.ingest.loader import load_match

TL = build_timeline(load_match("nbr-cal-seed42"))
SAKA, RICE, SALIBA, RAYA = "910109", "910108", "910105", "910101"
PALMER, PEDRO, SANCHEZ = "910210", "910209", "910201"


async def instant(_: float) -> None:
    await asyncio.sleep(0)


def run(session: DemoSession) -> list[dict]:
    sent: list[dict] = []

    async def send(env: dict) -> None:
        sent.append(env)

    asyncio.run(session.run(send, instant))
    return sent


def me_ht_pts(d: dict) -> int:
    return next(p for p in d["room"]["players"] if p["me"])["pts"]


def of(msgs: list[dict], kind: str) -> dict:
    return next(m["data"] for m in msgs if m["type"] == kind)


# --- squads -----------------------------------------------------------------


def test_squads_shape():
    s = bonus.squads()
    assert set(s["teams"]) == {"ARS", "CHE"}
    ars, che = s["teams"]["ARS"], s["teams"]["CHE"]
    assert (ars["name"], ars["popular"]) == ("Arsenal", SAKA)
    assert (che["name"], che["popular"]) == ("Chelsea", PALMER)
    assert (len(ars["players"]), len(che["players"])) == (16, 15)
    for team in (ars, che):
        assert sum(p["starter"] for p in team["players"]) == 11
        assert all(set(p) == {"id", "name", "num", "pos", "starter"} for p in team["players"])
    first = ars["players"][0]
    assert first == {"id": RAYA, "name": "Raya", "num": 1, "pos": "GK", "starter": True}
    by_name = {p["name"]: p for p in ars["players"] + che["players"]}
    assert by_name["Saka"]["pos"] == "FWD" and by_name["Rice"]["pos"] == "MID"
    assert by_name["Timber"] == {"id": "910120", "name": "Timber", "num": 12, "pos": "DEF",
                                 "starter": False}
    assert by_name["Palmer"]["pos"] == "MID" and by_name["João Pedro"]["pos"] == "FWD"
    assert [p["name"] for p in che["players"] if not p["starter"]] == [
        "Delap", "Gittens", "Lavia", "Gusto"]


def test_scoring_table_exact():
    sc = bonus.squads()["scoring"]
    assert sc["GK"] == {"label": "Goalkeeper", "earns": "saves, clean sheets", "rules": [
        ["Penalty save", 400], ["Clean sheet", 300], ["Save", 40], ["Claimed cross", 10]]}
    assert sc["DEF"]["rules"] == [["Goal", 400], ["Clean sheet", 200], ["Blocked shot", 30],
                                  ["Tackle won", 20], ["Interception", 20], ["Clearance", 10]]
    assert sc["MID"]["rules"] == [["Goal", 350], ["Assist", 200], ["Shot on target", 50],
                                  ["Key pass", 40], ["Tackle won", 20], ["Interception", 20]]
    assert sc["FWD"] == {"label": "Forward", "earns": "goals, shots", "rules": [
        ["Goal", 300], ["Assist", 200], ["Big chance made", 60], ["Shot on target", 50],
        ["Dribble past", 20], ["Won a foul", 10]]}
    assert sc["DEF"]["label"] == "Defender" and sc["MID"]["earns"] == "assists, key passes"


def test_get_squads_route():
    app = FastAPI()
    app.include_router(make_demo_router(sleep=instant))
    with TestClient(app) as client:
        r = client.get("/demo/squads")
    assert r.status_code == 200
    assert r.json() == bonus.squads()


# --- sanitising ---------------------------------------------------------------


@pytest.mark.parametrize("raw,want", [
    ([SAKA, RICE], [SAKA, RICE]),
    ([SAKA, SAKA, RICE], [SAKA, RICE]),
    ([SAKA, PALMER, RICE], [SAKA, RICE]),  # other side dropped
    (["999", SAKA], [SAKA]),
    ([SAKA, RICE, SALIBA], [SAKA, RICE]),  # extras beyond 2 dropped
    ([], []), (None, []), ("910109", []), (910109, []), ({"a": 1}, []),
    ([{"x": 1}, [1], 910109, None, True, SALIBA], [SALIBA]),
])
def test_sanitise_players(raw, want):
    assert bonus.sanitise("ARS", raw) == want


def test_sanitise_other_side():
    assert bonus.sanitise("CHE", [SAKA, PALMER, PEDRO, SANCHEZ]) == [PALMER, PEDRO]
    assert bonus.sanitise("XYZ", [SAKA]) == []


@pytest.mark.parametrize("players", [
    [PALMER, "nope", SAKA, SAKA, RICE, SALIBA], "x", 5, {"a": 1}, [[1], {"b": 2}], None])
def test_router_start_bad_players_never_closes(players):
    app = FastAPI()
    app.include_router(make_demo_router(sleep=instant))
    with TestClient(app) as client:
        with client.websocket_connect("/demo/ws") as ws:
            ws.send_json({"type": "start", "data": {"team": "ARS", "players": players}})
            first = ws.receive_json()
    assert first["type"] == "demo.session"
    you = first["data"]["you"]
    assert you["team"] == "ARS"
    want = [SAKA, RICE] if isinstance(players, list) and SAKA in players else []
    assert [p["id"] for p in you["players"]] == want


def test_router_start_without_players_key():
    app = FastAPI()
    app.include_router(make_demo_router(sleep=instant))
    with TestClient(app) as client:
        with client.websocket_connect("/demo/ws") as ws:
            ws.send_json({"type": "start", "data": {"team": "CHE"}})
            first = ws.receive_json()
    assert first["data"]["you"] == {"team": "CHE", "players": []}


# --- stats from raw events ------------------------------------------------------


def test_raw_stats_goals_by_half():
    st = bonus.match_stats()
    assert st.stats[(SAKA, 1)]["Goal"] == 1
    assert st.stats[(SAKA, 2)]["Goal"] == 0
    assert st.stats[(SALIBA, 2)]["Goal"] == 1
    assert st.stats[(SALIBA, 1)]["Goal"] == 0
    assert st.stats[(PEDRO, 2)]["Goal"] == 1
    assert st.conceded == {"ARS": 1, "CHE": 2}


def test_saka_first_half_goal_and_saliba_second_half_goal():
    saka_ht = bonus.player_bonus(SAKA, halves=(1,))
    assert {"what": "Goal", "count": 1, "pts": 300} in saka_ht["stats"]
    saliba_ht = bonus.player_bonus(SALIBA, halves=(1,))
    assert all(s["what"] != "Goal" for s in saliba_ht["stats"])
    saliba_ft = bonus.player_bonus(SALIBA, halves=(1, 2), clean_sheet=True)
    assert {"what": "Goal", "count": 1, "pts": 400} in saliba_ft["stats"]
    assert all(s["what"] != "Clean sheet" for s in saliba_ft["stats"])  # Chelsea scored


def test_player_bonus_shape_and_sum():
    for pid in (SAKA, RICE, SALIBA, RAYA, PALMER, PEDRO, SANCHEZ):
        b = bonus.player_bonus(pid, halves=(1, 2), clean_sheet=True)
        assert set(b) == {"id", "name", "num", "pos", "points", "stats"}
        assert b["points"] == sum(s["pts"] for s in b["stats"])
        assert all(s["count"] > 0 for s in b["stats"])
        ht = bonus.player_bonus(pid, halves=(1,))
        h2 = bonus.player_bonus(pid, halves=(2,))
        assert b["points"] == ht["points"] + h2["points"]


def test_unknown_player_bonus_is_zero():
    assert bonus.player_bonus("nope", halves=(1, 2))["points"] == 0


def test_clean_sheet_only_for_starters_of_a_team_that_kept_one():
    st = bonus.Stats(stats={}, conceded={"ARS": 0, "CHE": 3})
    assert bonus.player_bonus(RAYA, (1, 2), True, st)["stats"] == [
        {"what": "Clean sheet", "count": 1, "pts": 300}]
    assert bonus.player_bonus("910120", (1, 2), True, st)["points"] == 0  # Timber: bench
    assert bonus.player_bonus(SANCHEZ, (1, 2), True, st)["points"] == 0
    assert bonus.player_bonus(RAYA, (1,), False, st)["points"] == 0


# --- session ---------------------------------------------------------------------


def test_bonus_added_once_ht_then_rest_at_ft():
    plain = run(DemoSession(TL, "ARS", code="ABCD"))
    backed_s = DemoSession(TL, "ARS", code="ABCD", players=[SAKA, RICE])
    backed = run(backed_s)
    ht, ft = of(backed, "demo.half_time"), of(backed, "demo.full_time")
    ht_bonus = sum(p["points"] for p in ht["players"])
    ft_bonus = sum(p["points"] for p in ft["players"])
    assert ht["breakdown"]["players"] == ht_bonus
    assert ht["total"] == me_ht_pts(ht) == of(plain, "demo.half_time")["total"] + ht_bonus
    assert {"what": "Goal", "count": 1, "pts": 300} in ht["players"][0]["stats"]
    assert [p["id"] for p in ft["players"]] == [SAKA, RICE]
    assert ft_bonus == (bonus.player_bonus(SAKA, (1, 2), True)["points"]
                        + bonus.player_bonus(RICE, (1, 2), True)["points"])
    me_ht = lambda d: next(p for p in d["room"]["players"] if p["me"])["pts"]  # noqa: E731
    assert me_ht(ht) - me_ht(of(plain, "demo.half_time")) == ht_bonus
    assert ft["total"] - of(plain, "demo.full_time")["total"] == ft_bonus
    assert backed_s.total == ft["total"]
    pb = next(r for r in ft["rows"] if r["key"] == "pb")
    assert pb == {"key": "pb", "label": "PLAYER BONUS", "points": ft_bonus,
                  "detail": "Earned by the 2 players you backed"}
    rows = {r["key"]: r["points"] for r in ft["rows"]}
    assert rows["rt"] + rows["fc"] + rows["pb"] == ft["total"]
    # friends unchanged
    friends = lambda d: {p["name"]: p["pts"] for p in d["room"]["players"] if not p["me"]}  # noqa: E731
    assert friends(ft) == friends(of(plain, "demo.full_time"))
    assert friends(ht) == friends(of(plain, "demo.half_time"))
    you = of(backed, "demo.session")["you"]
    assert you == {"team": "ARS", "players": [
        {"id": SAKA, "name": "Saka", "num": 7, "pos": "FWD"},
        {"id": RICE, "name": "Rice", "num": 41, "pos": "MID"}]}


def test_router_warms_stats_with_timeline(monkeypatch):
    calls = []
    real = bonus.match_stats
    monkeypatch.setattr(bonus, "match_stats", lambda *a: calls.append(1) or real(*a))
    async def stuck(_: float) -> None:  # the match never gets past its first cue
        await asyncio.sleep(3600)

    app = FastAPI()
    app.include_router(make_demo_router(sleep=stuck))
    with TestClient(app) as client:
        with client.websocket_connect("/demo/ws") as ws:
            ws.send_json({"type": "start", "data": {"team": "ARS", "players": [SAKA]}})
            assert ws.receive_json()["type"] == "demo.session"
            assert calls  # read before the session started, not at half time


def test_saliba_goal_lands_at_full_time_only():
    msgs = run(DemoSession(TL, "ARS", code="ABCD", players=[SALIBA]))
    ht, ft = of(msgs, "demo.half_time"), of(msgs, "demo.full_time")
    assert all(s["what"] != "Goal" for s in ht["players"][0]["stats"])
    assert {"what": "Goal", "count": 1, "pts": 400} in ft["players"][0]["stats"]
    assert next(r for r in ft["rows"] if r["key"] == "pb")["detail"] == (
        "Earned by the player you backed")


def test_no_players_backed():
    msgs = run(DemoSession(TL, "CHE", code="ABCD"))
    ht, ft = of(msgs, "demo.half_time"), of(msgs, "demo.full_time")
    assert ht["players"] == [] and ht["breakdown"]["players"] == 0
    assert ft["players"] == []
    assert next(r for r in ft["rows"] if r["key"] == "pb") == {
        "key": "pb", "label": "PLAYER BONUS", "detail": "Back players next match", "points": 0}


def test_session_sanitises_players_itself():
    s = DemoSession(TL, "CHE", code="ABCD", players=[SAKA, PALMER, PALMER, "x", PEDRO, SANCHEZ])
    assert s.players == [PALMER, PEDRO]
