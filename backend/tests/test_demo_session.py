"""test_demo_session.py — DemoSession scoring and the /demo/ws wire protocol.

Scoring order is checked on DemoSession directly (deterministic); the router tests check the
protocol, bad input, the session cap and cleanup through a TestClient websocket.
"""

from __future__ import annotations

import asyncio
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from backend.app.main import create_app
from backend.demo.names import TEAMS
from backend.demo.router import make_demo_router
from backend.demo.script import build_timeline
from backend.demo.session import HALF_TIME_HOLD_S, DemoSession
from backend.ingest.loader import load_match
from backend.tests.fixtures.replay_fixtures import FakeSource, calm, tiny_bundle

TL = build_timeline(load_match("nbr-cal-seed42"))
ANSWERS = [c.answer for c in TL.calls.values()]


async def instant(_: float) -> None:
    await asyncio.sleep(0)


async def tick(delay: float) -> None:
    await asyncio.sleep(0.0005 if delay else 0)


async def stall(_: float) -> None:
    await asyncio.Event().wait()  # never set: the session stays on its first sleep


def run_session(session: DemoSession, on_open=None, on_lock=None) -> list[dict]:
    """Run a session with an instant sleep; hooks fire inside ``send`` per message type."""
    sent: list[dict] = []

    async def send(env: dict) -> None:
        sent.append(env)
        if env["type"] == "call.open" and on_open:
            on_open(env["data"]["call_id"])
        if env["type"] == "call.result" and on_lock:
            on_lock(env["data"]["call_id"])

    asyncio.run(session.run(send, instant))
    return sent


def results(msgs: list[dict]) -> list[dict]:
    return [m["data"] for m in msgs if m["type"] == "call.result"]


def drain(ws) -> tuple[list[dict], int]:
    """All messages until the server closes; returns them and the close code."""
    msgs: list[dict] = []
    with pytest.raises(WebSocketDisconnect) as gone:
        for _ in range(10_000):
            msgs.append(ws.receive_json())
    return msgs, gone.value.code


def router_client(**kw) -> tuple[TestClient, object]:
    router = make_demo_router(**kw)
    app = FastAPI()
    app.include_router(router)
    return TestClient(app), router


def wait_until(cond, timeout: float = 3.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.01)
    return cond()


@pytest.fixture()
def client():
    app = create_app(FakeSource(tiny_bundle(calm(1, 5))), sleep=instant)
    with TestClient(app) as c:
        yield c


START_ARS = {"type": "start", "data": {"team": "ARS"}}


@pytest.fixture(autouse=True)
def short_post_match(monkeypatch):
    """The socket stays open POST_MATCH_S after full time; keep drains to full time quick."""
    import backend.demo.router as router_module
    monkeypatch.setattr(router_module, "POST_MATCH_S", 0.05)


# --- DemoSession -------------------------------------------------------------


def test_plays_to_full_time_with_results():
    session = DemoSession(TL, "ARS")
    msgs = run_session(session, on_open=lambda cid: session.answer(cid, "SHOOT"))
    assert msgs[0]["type"] == "demo.session" and msgs[-1]["type"] == "demo.full_time"
    res = results(msgs)
    assert ANSWERS == ["PASS", "DRIBBLE", "SHOOT", "SHOOT", "PASS", "SHOOT"]
    assert len(res) == 6 and sum(r["correct"] for r in res) == 3
    # correct at calls 3, 4, 6; streak peaks at 2, so no bonus
    assert res[-1]["total"] == 6 * 10 + 3 * 100 == 360 == session.total
    assert [r["answer"] for r in res] == ANSWERS and all(r["pick"] == "SHOOT" for r in res)
    assert session.streak == 1 and session.open_call is None


def test_always_pass_scores_less_than_always_shoot():
    session = DemoSession(TL, "ARS")
    res = results(run_session(session, on_open=lambda cid: session.answer(cid, "PASS")))
    # correct at calls 1 and 5 only
    assert [r["correct"] for r in res] == [True, False, False, False, True, False]
    assert res[-1]["total"] == 6 * 10 + 2 * 100 == 260 == session.total


def test_session_message_and_lock_pairs():
    msgs = run_session(DemoSession(TL, "CHE"))
    first = msgs[0]["data"]
    assert first["you"] == {"team": "CHE", "players": []} and first["duration"] == TL.duration
    assert first["teams"] == {"home": TEAMS["NBR"], "away": TEAMS["CAL"]}
    types = [m["type"] for m in msgs]
    assert "call.lock" not in types
    for i, t in enumerate(types):
        if t == "call.locked":
            assert types[i + 1] == "call.result"
            assert msgs[i + 1]["data"]["call_id"] == msgs[i]["data"]["call_id"]
    assert types.count("call.locked") == 6 and types.count("call.open") == 6


def test_sleeps_sum_to_duration_and_skip_zero():
    slept: list[float] = []

    async def record(delay: float) -> None:
        slept.append(delay)

    async def send(_: dict) -> None:
        pass

    asyncio.run(DemoSession(TL, "ARS").run(send, record))
    # nobody closes the save sheet: the half-time hold replaces the scripted 10 s pause
    assert all(d > 0 for d in slept)
    assert sum(slept) == pytest.approx(TL.duration - 10 + HALF_TIME_HOLD_S)
    assert HALF_TIME_HOLD_S in slept


def test_half_time_skip_starts_the_second_half():
    slept: list[float] = []
    session = DemoSession(TL, "ARS")

    async def record(delay: float) -> None:
        slept.append(delay)
        if delay == HALF_TIME_HOLD_S:
            await asyncio.Event().wait()  # only half_time.skip ends this hold

    async def send(env: dict) -> None:
        if env["type"] == "demo.half_time":
            assert env["data"]["seconds"] == HALF_TIME_HOLD_S
            asyncio.get_running_loop().call_soon(session.skip_half_time)

    assert session.skip_half_time() is False  # not half time yet
    asyncio.run(asyncio.wait_for(session.run(send, record), 5))
    assert sum(d for d in slept if d != HALF_TIME_HOLD_S) == pytest.approx(TL.duration - 10)


def test_double_answer_counts_once():
    session = DemoSession(TL, "ARS")
    second: list[bool] = []

    def on_open(cid: str) -> None:
        assert session.answer(cid, TL.calls[cid].answer) is True
        second.append(session.answer(cid, TL.calls[cid].answer))

    res = results(run_session(session, on_open=on_open))
    assert second == [False] * 6
    assert all(r["correct"] for r in res)
    # 6 correct in a row: 6 * 110 + streak bonus 50 * (1 + 2 + 3 + 4); beats always-SHOOT / PASS
    assert session.total == 6 * 110 + 50 * 10 == 1160 > 360 > 260


def test_answer_after_lock_ignored():
    session = DemoSession(TL, "ARS")
    late: list[bool] = []

    def on_lock(cid: str) -> None:
        late.append(session.answer(cid, TL.calls[cid].answer))

    res = results(run_session(session, on_lock=on_lock))
    assert late == [False] * 6
    assert all(r["too_quick"] and r["pick"] is None for r in res) and session.total == 0


def test_answer_rejects_unknown_call_and_bad_pick():
    session = DemoSession(TL, "ARS")
    seen: list[bool] = []

    def on_open(cid: str) -> None:
        seen.extend([session.answer("nope", "PASS"), session.answer(cid, "shoot"),
                     session.answer(cid, ["PASS"]), session.answer(None, None)])

    run_session(session, on_open=on_open)
    assert seen == [False] * 24 and session.total == 0


# --- /demo/ws ----------------------------------------------------------------


def test_no_answers_reaches_full_time(client):
    with client.websocket_connect("/demo/ws") as ws:
        ws.send_json({"type": "start", "data": {"team": "CHE"}})
        msgs, code = drain(ws)
    assert msgs[0]["type"] == "demo.session" and msgs[0]["data"]["you"] == {"team": "CHE", "players": []}
    assert msgs[-1]["type"] == "demo.full_time" and code == 1000
    res = results(msgs)
    assert len(res) == 6 and all(r["too_quick"] for r in res) and res[-1]["total"] == 0


def test_bad_messages():
    client, _ = router_client(sleep=tick)
    with client:
        for first in ("not json", '{"type": "call.answer", "data": {}}',
                      '{"type": "start", "data": {"team": "NBR"}}', '{"type": "start"}', "[1]"):
            with client.websocket_connect("/demo/ws") as ws:
                ws.send_text(first)
                msgs, code = drain(ws)
            assert msgs == [] and code == 1008, first
        with client.websocket_connect("/demo/ws") as ws:
            ws.send_json(START_ARS)
            for junk in ("garbage", "[]", '{"type": "nope"}', '{"type": "call.answer"}',
                         '{"type": "call.answer", "data": {"call_id": "x", "pick": "PASS"}}',
                         '{"type": "call.answer", "data": {"call_id": [1], "pick": {}}}',
                         '{"type": "call.answer", "data": "x"}'):
                ws.send_text(junk)
            msgs, code = drain(ws)
        assert msgs[-1]["type"] == "demo.full_time" and code == 1000


def test_session_cap():
    client, router = router_client(sleep=stall, max_sessions=1)
    with client:
        with client.websocket_connect("/demo/ws") as one:
            one.send_json(START_ARS)
            assert one.receive_json()["type"] == "demo.session"
            with client.websocket_connect("/demo/ws") as two:
                with pytest.raises(WebSocketDisconnect) as full:
                    two.receive_json()
            assert full.value.code == 1013
            assert len(router.active) == 1


def test_disconnect_cancels_session():
    client, router = router_client(sleep=stall)
    with client:
        with client.websocket_connect("/demo/ws") as ws:
            ws.send_json(START_ARS)
            assert ws.receive_json()["type"] == "demo.session"
            assert len(router.active) == 1
        assert wait_until(lambda: len(router.active) == 0)
        with client.websocket_connect("/demo/ws") as ws:  # a fresh connect starts over
            ws.send_json(START_ARS)
            assert ws.receive_json()["type"] == "demo.session"
            assert ws.receive_json()["type"] == "demo.jump"  # the intro card
        assert wait_until(lambda: len(router.active) == 0)


def test_max_sessions_default_from_env(monkeypatch):
    monkeypatch.setenv("MAX_DEMO_SESSIONS", "0")
    client, _ = router_client(sleep=stall)
    with client, client.websocket_connect("/demo/ws") as ws:
        with pytest.raises(WebSocketDisconnect) as full:
            ws.receive_json()
    assert full.value.code == 1013


def test_start_must_arrive_in_time(monkeypatch):
    import backend.demo.router as router_module
    monkeypatch.setattr(router_module, "START_TIMEOUT_S", 0.2)
    client, router = router_client(sleep=stall)
    with client, client.websocket_connect("/demo/ws") as ws:
        msgs, code = drain(ws)
    assert msgs == [] and code == 1008
    assert wait_until(lambda: len(router.active) == 0)
