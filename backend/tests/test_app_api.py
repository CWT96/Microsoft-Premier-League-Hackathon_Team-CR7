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
import json
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


FAST_RATE = (10_000.0, 10_000.0)  # tests run far faster than a phone


async def fast_sleep(delay: float) -> None:
    await asyncio.sleep(0.0005 if delay else 0)


def short_states():
    return ([st(0.0, mode="lobby", situation="kickoff")] + calm(1, 40)
            + [st(40.0, mode="full_time", situation="full_time")])


@pytest.fixture()
def client():
    app = create_app(FakeSource(tiny_bundle(short_states())), sleep=fast_sleep, speed=15,
                     rate_limit=FAST_RATE)
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
from pydantic import ValidationError

from backend.app.main import DEFAULT_MAX_ROOMS, DefaultSource, Join, Outbox


def make_client(**kw):
    app = create_app(FakeSource(tiny_bundle(short_states())), sleep=fast_sleep, speed=15,
                     **{"rate_limit": FAST_RATE, **kw})
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
    app = create_app(FakeSource(tiny_bundle(short_states())), sleep=sleep, speed=15, propose=spy,
                     rate_limit=FAST_RATE)
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
        rid = client.post("/api/rooms").json()["room_id"]
        for _ in range(2):
            with client.websocket_connect(f"/ws?room_id={rid}") as ws:
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
            assert rid not in app.state.rooms or app.state.rooms[rid].done


def test_cors_headers_and_readyz():
    client, _ = make_client()
    with client:
        r = client.get("/healthz", headers={"Origin": "http://localhost:5173"})
        assert r.headers["access-control-allow-origin"] == "*"
        ready = client.get("/readyz")  # generic body only
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
        assert ready.status_code == 200 and ready.json() == {"ready": True}
        assert "dataset missing" not in ready.text
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
        r1 = client.post("/api/rooms").json()["room_id"]
        r2 = client.post("/api/rooms").json()["room_id"]
        with client.websocket_connect(f"/ws?room_id={r1}") as one:
            one.send_json(JOIN)
            assert one.receive_json()["type"] == "session"
            with client.websocket_connect(f"/ws?room_id={r2}") as two:
                two.send_json(JOIN)
                with pytest.raises(WebSocketDisconnect) as full:
                    two.receive_json()
            assert full.value.code == main_module.WS_CLOSE_BUSY
    assert DEFAULT_MAX_ROOMS == 200


def test_signup_rejects_bad_or_oversized_email(client):
    assert client.post("/api/signup", json={"user_id": "u", "email": "nope"}).status_code == 422
    long = "a" * 250 + "@b.co"
    assert client.post("/api/signup", json={"user_id": "u", "email": long}).status_code == 422
    assert client.post("/api/signup", json={"user_id": "u", "email": "a@b.co"}).status_code == 200


# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
#
# Final review: abuse limits, random ids, generic errors
#
# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%


def disconnect_code(ws) -> int:
    with pytest.raises(WebSocketDisconnect) as gone:
        for _ in range(100_000):
            ws.receive_json()
    return gone.value.code


def test_oversized_frame_closes_the_socket_with_1009():
    client, _ = make_client()
    with client:
        with client.websocket_connect("/ws") as ws:  # in the join
            ws.send_text(json.dumps({"type": "join", "data": {"display_name": "x" * 5000}}))
            assert disconnect_code(ws) == main_module.WS_CLOSE_TOO_BIG
        with client.websocket_connect("/ws") as ws:  # after the join
            ws.send_json(JOIN)
            assert ws.receive_json()["type"] == "session"
            ws.send_text(json.dumps({"type": "answer", "data": {"qid": "q" * 5000}}))
            assert disconnect_code(ws) == main_module.WS_CLOSE_TOO_BIG


def test_token_bucket_allows_a_burst_then_the_sustained_rate():
    clock = [0.0]
    bucket = main_module.TokenBucket(rate=10, burst=20, clock=lambda: clock[0])
    assert sum(bucket.allow() for _ in range(30)) == 20
    clock[0] += 0.5
    assert sum(bucket.allow() for _ in range(30)) == 5
    clock[0] += 10
    assert sum(bucket.allow() for _ in range(30)) == 20


def test_flooding_a_socket_closes_it_with_1008():
    app = create_app(FakeSource(tiny_bundle(short_states())), sleep=fast_sleep, speed=15)
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        ws.send_json(JOIN)
        for _ in range(60):
            ws.send_json({"type": "feed.resume", "data": {}})
        assert disconnect_code(ws) == main_module.WS_CLOSE_POLICY


def test_sockets_per_ip_are_capped_including_sockets_that_have_not_joined():
    client, _ = make_client(max_sockets_per_ip=2)
    with client:
        with client.websocket_connect("/ws") as idle1, client.websocket_connect("/ws") as idle2:
            with client.websocket_connect("/ws") as third:
                assert disconnect_code(third) == main_module.WS_CLOSE_BUSY
            idle1.send_json(JOIN)
            assert idle1.receive_json()["type"] == "session"
            idle2.send_json(JOIN)
            assert idle2.receive_json()["type"] == "session"
        with client.websocket_connect("/ws") as again:  # the slots were released
            again.send_json(JOIN)
            assert again.receive_json()["type"] == "session"


def test_room_member_cap_turns_the_extra_human_away(monkeypatch):
    monkeypatch.setenv("MAX_ROOM_MEMBERS", "2")
    client, _ = make_client()
    with client:
        rid = client.post("/api/rooms").json()["room_id"]
        with client.websocket_connect(f"/ws?room_id={rid}") as a, \
                client.websocket_connect(f"/ws?room_id={rid}") as b, \
                client.websocket_connect(f"/ws?room_id={rid}") as c:
            for ws in (a, b):
                ws.send_json(JOIN)
                assert ws.receive_json()["type"] == "session"
            c.send_json(JOIN)
            assert disconnect_code(c) == main_module.WS_CLOSE_BUSY


def test_room_ids_are_random_registered_and_required_for_a_join():
    client, app = make_client()
    with client:
        a = client.post("/api/rooms").json()["room_id"]
        b = client.post("/api/rooms").json()["room_id"]
        assert a != b and a.startswith("r-") and len(a) >= 10 and a in app.state.reserved
        with client.websocket_connect("/ws?room_id=r-guessed") as ws:
            ws.send_json(JOIN)
            assert disconnect_code(ws) == main_module.WS_CLOSE_UNKNOWN_ROOM
        with client.websocket_connect(f"/ws?room_id={a}") as ws:
            ws.send_json(JOIN)
            assert ws.receive_json()["data"]["room_id"] == a
        with client.websocket_connect("/ws") as ws:  # no room asked for: a private solo room
            ws.send_json(JOIN)
            solo = ws.receive_json()["data"]
            assert solo["is_host"] is True and solo["room_id"].startswith("r-")


def test_member_ids_are_random_and_the_signup_store_evicts_the_oldest(monkeypatch):
    monkeypatch.setattr(main_module, "SIGNUP_CAP", 3)
    client, _ = make_client()
    with client:
        ids = [client.post("/api/signup", json={"user_id": "u", "email": "a@b.co"}).json()
               ["member_id"] for _ in range(5)]
        assert len(set(ids)) == 5 and all(len(i) >= 10 for i in ids)
        assert client.get(f"/api/card/{ids[0]}").status_code == 404
        assert client.get(f"/api/card/{ids[-1]}").status_code == 200


def test_errors_do_not_leak_internals():
    class Broken(FakeSource):
        def get(self, match_id):
            raise FileNotFoundError("/secret/data/path/events.jsonl")
    app = create_app(Broken(tiny_bundle(short_states())))
    with TestClient(app) as client:
        r = client.get("/api/matches")
        assert r.status_code == 503 and "secret" not in r.text and "jsonl" not in r.text
        assert "secret" not in client.get("/readyz").text


def test_join_fields_are_bounded_and_unknown_players_are_ignored():
    with pytest.raises(ValidationError):
        Join(team="x" * 33)
    with pytest.raises(ValidationError):
        Join(player_id="y" * 33)
    client, app = make_client()
    with client, client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "join", "data": {"match_id": "tiny", "player_id": "nobody-here",
                                  "team": "NBR"}})
        assert ws.receive_json()["type"] == "session"
        room = next(iter(app.state.rooms.values()))
        assert room.humans[0].player_id is None


def test_reaper_drops_empty_rooms_and_stale_lobbies():
    client, app = make_client()
    with client:
        with client.websocket_connect("/ws") as ws:
            ws.send_json(JOIN)
            ws.receive_json()
            room = next(iter(app.state.rooms.values()))
            room.pause.started -= 1000  # lobby unresumed for ~17 minutes
            assert app.state.reap() == 1 and app.state.rooms == {}
        # a room nobody ever joined
        from backend.app.replay import Room
        ghost = Room("r-ghost", tiny_bundle(short_states()))
        ghost.empty_since -= 120
        app.state.rooms["r-ghost"] = ghost
        assert app.state.reap() == 1 and "r-ghost" not in app.state.rooms


def test_ip_cap_keys_on_x_forwarded_for_behind_the_proxy(monkeypatch):
    monkeypatch.delenv("TRUST_PROXY", raising=False)  # default: trusted
    client, _ = make_client(max_sockets_per_ip=1)
    with client:
        with client.websocket_connect("/ws", headers={"X-Forwarded-For": "1.1.1.1, 10.0.0.1"}):
            with client.websocket_connect("/ws", headers={"X-Forwarded-For": "2.2.2.2"}) as b:
                b.send_json(JOIN)
                assert b.receive_json()["type"] == "session"  # a different client has its own cap
            with client.websocket_connect("/ws", headers={"X-Forwarded-For": "1.1.1.1"}) as c:
                assert disconnect_code(c) == main_module.WS_CLOSE_BUSY  # same client shares it


def test_ip_cap_uses_the_peer_when_the_proxy_is_not_trusted(monkeypatch):
    monkeypatch.setenv("TRUST_PROXY", "false")
    client, _ = make_client(max_sockets_per_ip=1)
    with client:
        with client.websocket_connect("/ws", headers={"X-Forwarded-For": "1.1.1.1"}):
            with client.websocket_connect("/ws", headers={"X-Forwarded-For": "2.2.2.2"}) as b:
                assert disconnect_code(b) == main_module.WS_CLOSE_BUSY
