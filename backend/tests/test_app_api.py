"""test_app_api.py — FastAPI endpoints and one WebSocket session, payloads checked by contract.

Last Modified: 2026-10-24 23:59, first version
Inputs:  hand-built tiny match (fixtures/replay_fixtures.py), no network
Outputs: none

Workflow:
  - REST via TestClient; a WebSocket session joins, resumes the lobby, answers cards and
    plays to full time with an instant sleep function
  - every message with a contract schema is validated

CAUTION: none
"""

from __future__ import annotations

import asyncio
import time

import pytest
from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.tests.contract_utils import validate
from backend.tests.fixtures.replay_fixtures import FakeSource, calm, tiny_bundle
from backend.tests.fixtures.timing_scenarios import st

SCHEMAS = {"question.open": "question", "question.result": "result", "score.final": "score",
           "match.state": "state", "match.tracking": "tracking", "match.event": "event",
           "timing.decision": "timing_decision", "nudge": "nudge"}


async def fast_sleep(delay: float) -> None:
    await asyncio.sleep(0.0005 if delay else 0)


def short_states():
    return ([st(0.0, mode="lobby", situation="kickoff")] + calm(1, 40)
            + [st(40.0, mode="full_time", situation="full_time")])


@pytest.fixture()
def client():
    app = create_app(FakeSource(tiny_bundle(short_states())), sleep=fast_sleep, speed=15)
    with TestClient(app) as c:
        yield c


JOIN = {"type": "join", "data": {"match_id": "tiny", "room_id": None, "display_name": "Tia",
                                 "team": "NBR", "player_id": "nbr-1-ward"}}


def test_healthz_and_matches(client):
    assert client.get("/healthz").json() == {"ok": True}
    body = client.get("/api/matches").json()
    assert body[0]["match_id"] == "tiny" and body[0]["home"] == "NBR"
    assert {"match_id", "home", "away", "kickoff_label"} <= set(body[0])


def test_players_endpoint(client):
    players = client.get("/api/matches/tiny/players").json()
    assert {"player_id": "nbr-1-ward", "name": "Ward", "team": "NBR", "position": "GK"} in players
    assert client.get("/api/matches/nope/players").status_code == 404


def test_rooms_endpoint_returns_join_url(client):
    body = client.post("/api/rooms").json()
    assert body["room_id"].startswith("r-") and body["room_id"] in body["join_url"]


def test_ops_funnel_is_monotone(client):
    f = client.get("/api/ops/funnel", params={"match_id": "tiny"}).json()
    assert set(f) == {"scanned", "answered_5plus", "signed_up", "redeemed", "new_members"}
    assert f["scanned"] >= f["answered_5plus"] >= f["signed_up"] >= f["redeemed"]


def test_signup_card_and_unknown_score(client):
    assert client.get("/api/score/u-nobody").status_code == 404
    assert client.get("/api/card/m-none").status_code == 404
    s = client.post("/api/signup", json={"user_id": "u-x", "email": "a@b.c"}).json()
    assert s["member_id"].startswith("m-") and s["member_id"] in s["card_url"]
    card = client.get(f"/api/card/{s['member_id']}").json()
    assert card["final_score"] == 0 and card["tier"] == "Rookie"
    assert {"member_id", "display_name", "final_score", "qr_payload", "tier"} <= set(card)


def test_bad_first_message_closes_the_socket(client):
    with client.websocket_connect("/ws?match_id=tiny") as ws:
        ws.send_json({"type": "answer", "data": {}})
        with pytest.raises(Exception):
            ws.receive_json()


def test_websocket_session_to_full_time_then_rest_score_and_card(client):
    seen: list[dict] = []
    with client.websocket_connect("/ws?match_id=tiny&room_id=") as ws:
        ws.send_json(JOIN)
        session = ws.receive_json()
        assert session["type"] == "session" and session["data"]["is_host"] is True
        user_id = session["data"]["user_id"]
        assert len(session["data"]["friends"]) == 3
        resumed = False
        while True:
            msg = ws.receive_json()
            seen.append(msg)
            if msg["type"] == "match.state" and not resumed:
                assert msg["data"]["mode"] in ("lobby", "game")
                ws.send_json({"type": "control", "data": {"action": "resume", "value": None}})
                resumed = True
            if msg["type"] == "question.open":
                ws.send_json({"type": "hint.request", "data": {"qid": msg["data"]["qid"]}})
                ws.send_json({"type": "answer", "data": {
                    "qid": msg["data"]["qid"], "option": msg["data"]["options"][0]["id"]}})
            if msg["type"] == "leaderboard" and msg["data"]["scope"] == "global":
                break
    types = {m["type"] for m in seen}
    assert {"match.state", "question.open", "hint", "question.result", "score.final",
            "leaderboard", "match.event", "timing.decision"} <= types
    for m in seen:
        if m["type"] in SCHEMAS:
            validate(SCHEMAS[m["type"]], m["data"])
    final = client.get(f"/api/score/{user_id}").json()
    validate("score", final)
    assert final["user_id"] == user_id and final["hints_used"] >= 1
    member = client.post("/api/signup", json={"user_id": user_id, "email": "a@b.c"}).json()
    card = client.get(f"/api/card/{member['member_id']}").json()
    assert card["display_name"] == "Tia" and card["final_score"] == final["final_score"]


def test_unknown_match_closes_the_socket(client):
    with client.websocket_connect("/ws?match_id=nope") as ws:
        ws.send_json({"type": "join", "data": {"match_id": "nope"}})
        with pytest.raises(Exception):
            ws.receive_json()


# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
#
# Review round 1: supervision, back-pressure, threads, hygiene
#
# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

import threading

from starlette.websockets import WebSocketDisconnect

from backend.app import main as main_module
from backend.app.main import DEFAULT_MAX_ROOMS, DefaultSource, Join, Outbox


def make_client(**kw):
    app = create_app(FakeSource(tiny_bundle(short_states())), sleep=fast_sleep, speed=15, **kw)
    return TestClient(app), app


def test_outbox_drops_stale_positions_first_then_overflows():
    async def go():
        box = Outbox(maxsize=4)
        box.push({"type": "match.state", "data": {}})
        box.push({"type": "match.tracking", "data": {}})
        box.push({"type": "question.open", "data": {}})
        box.push({"type": "match.state", "data": {}})
        box.push({"type": "question.result", "data": {}})  # full: stale kinds go, this fits
        assert not box.overflowed
        got = [await box.get() for _ in range(2)]
        assert [g["type"] for g in got] == ["question.open", "question.result"]
        for _ in range(5):  # nothing stale left to drop
            box.push({"type": "question.open", "data": {}})
        assert box.overflowed and await box.get() is None
        done = Outbox()
        done.push({"type": "nudge", "data": {}})
        done.close()
        assert (await done.get())["type"] == "nudge" and await done.get() is None
    asyncio.run(go())


def test_ticks_run_off_the_event_loop_thread():
    loop_threads, tick_threads = set(), set()

    async def sleep(delay):
        loop_threads.add(threading.get_ident())
        await fast_sleep(delay)

    from backend.agents.question_generator_stub import propose as stub

    def spy(state, moment, pid, roster=None):
        tick_threads.add(threading.get_ident())
        return stub(state, moment, pid, roster=roster)
    app = create_app(FakeSource(tiny_bundle(short_states())), sleep=sleep, speed=15, propose=spy)
    with TestClient(app) as c, c.websocket_connect("/ws") as ws:
        ws.send_json(JOIN)
        for _ in range(6):
            ws.receive_json()
    assert tick_threads and loop_threads and not (tick_threads & loop_threads)


def test_default_source_builds_each_match_once_under_threads():
    calls = []

    def builder(match_id):
        calls.append(match_id)
        return tiny_bundle(short_states())
    src = DefaultSource(builder)
    src.match_ids = lambda: ["tiny"]
    threads = [threading.Thread(target=src.get, args=("tiny",)) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert calls == ["tiny"]


def test_crashed_room_is_dropped_sockets_close_and_a_rejoin_gets_a_fresh_room():
    def broken(state, moment, pid, roster=None):
        raise RuntimeError("generator exploded")
    client, app = make_client(propose=broken)
    with client:
        with client.websocket_connect("/ws") as ws:
            ws.send_json(JOIN)
            assert ws.receive_json()["type"] == "session"
            with pytest.raises(WebSocketDisconnect):
                for _ in range(10_000):
                    ws.receive_json()
        assert app.state.rooms == {}
        with client.websocket_connect("/ws") as ws:
            ws.send_json(JOIN)
            again = ws.receive_json()
            assert again["type"] == "session" and again["data"]["is_host"] is True


def test_finished_room_is_dropped_so_the_same_room_id_restarts():
    client, app = make_client()
    with client:
        for _ in range(2):
            with client.websocket_connect("/ws?room_id=r-again") as ws:
                ws.send_json(JOIN)
                assert ws.receive_json()["data"]["is_host"] is True
                ws.send_json({"type": "control", "data": {"action": "resume", "value": None}})
                last = None
                while last != "leaderboard":
                    msg = ws.receive_json()
                    last = msg["type"]
                    if last == "question.open":
                        ws.send_json({"type": "answer", "data": {
                            "qid": msg["data"]["qid"], "option": msg["data"]["options"][0]["id"]}})
                    if msg["type"] == "leaderboard" and msg["data"]["scope"] != "global":
                        last = None
            assert "r-again" not in app.state.rooms or app.state.rooms["r-again"].done


def test_cors_headers_and_readyz():
    client, _ = make_client()
    with client:
        r = client.get("/healthz", headers={"Origin": "http://localhost:5173"})
        assert r.headers["access-control-allow-origin"] == "*"
        ready = client.get("/readyz")
        assert ready.status_code in (200, 503)


def test_warm_up_failure_is_not_fatal(monkeypatch):
    def failing(_match_id):
        raise FileNotFoundError("dataset missing")
    monkeypatch.setattr(main_module, "DefaultSource", lambda: DefaultSource(failing))
    app = create_app()
    with TestClient(app) as c:
        assert c.get("/healthz").json() == {"ok": True}
        for _ in range(100):
            ready = c.get("/readyz")
            if ready.status_code == 200:
                break
            time.sleep(0.05)
        assert ready.status_code == 200 and "dataset missing" in ready.json()["warmup_error"]
        assert c.get("/api/matches").status_code == 503


def test_join_must_arrive_in_time_and_room_id_must_be_valid(monkeypatch):
    monkeypatch.setattr(main_module, "JOIN_TIMEOUT_S", 0.2)
    client, _ = make_client()
    with client:
        with client.websocket_connect("/ws") as ws:
            with pytest.raises(WebSocketDisconnect) as quiet:
                ws.receive_json()
        assert quiet.value.code == main_module.WS_CLOSE_JOIN_TIMEOUT
        with client.websocket_connect("/ws") as ws:
            ws.send_json({"type": "join", "data": {"room_id": "bad id/../x"}})
            with pytest.raises(WebSocketDisconnect) as bad:
                ws.receive_json()
        assert bad.value.code == main_module.WS_CLOSE_BAD_JOIN


def test_display_name_is_trimmed_to_24_characters():
    assert len(Join(display_name="x" * 80).display_name) == 24
    assert Join(display_name="   ").display_name == "Fan"


def test_room_cap_turns_new_rooms_away():
    client, _ = make_client(max_rooms=1)
    with client:
        with client.websocket_connect("/ws?room_id=r-one") as one:
            one.send_json(JOIN)
            assert one.receive_json()["type"] == "session"
            with client.websocket_connect("/ws?room_id=r-two") as two:
                two.send_json(JOIN)
                with pytest.raises(WebSocketDisconnect) as full:
                    two.receive_json()
            assert full.value.code == main_module.WS_CLOSE_BUSY
    assert DEFAULT_MAX_ROOMS == 50


def test_signup_rejects_bad_or_oversized_email(client):
    assert client.post("/api/signup", json={"user_id": "u", "email": "nope"}).status_code == 422
    long = "a" * 250 + "@b.co"
    assert client.post("/api/signup", json={"user_id": "u", "email": long}).status_code == 422
    assert client.post("/api/signup", json={"user_id": "u", "email": "a@b.co"}).status_code == 200
