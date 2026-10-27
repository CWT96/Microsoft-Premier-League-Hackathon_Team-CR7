"""test_demo_forecast_session.py — forecasts in the demo: picks, settlement and the shared total.

Drives DemoSession directly with an instant sleep; a hook inside ``send`` places picks the way
the router would (the session method's reply is appended to the sent list, like the router's
``send_json``). One router test checks the wire routing of forecast messages.
"""

from __future__ import annotations

import asyncio

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.demo.forecasts import WINDOWS
from backend.demo.router import make_demo_router
from backend.demo.script import build_timeline
from backend.demo.session import DemoSession
from backend.ingest.loader import load_match

TL = build_timeline(load_match("nbr-cal-seed42"))
START_ARS = {"type": "start", "data": {"team": "ARS"}}


async def instant(_: float) -> None:
    await asyncio.sleep(0)


async def stall(_: float) -> None:
    await asyncio.Event().wait()


def run_with(session: DemoSession, hook) -> list[dict]:
    """Run with an instant sleep; ``hook(env, reply)`` fires after each message is captured.

    ``reply(msg)`` appends a session method's return value, as the router would send it.
    """
    sent: list[dict] = []

    async def send(env: dict) -> None:
        sent.append(env)
        hook(env, sent.append)

    asyncio.run(session.run(send, instant))
    return sent


def at_clock(clock: str, action):
    """Hook running ``action(reply)`` once, at the first demo.clock showing ``clock``."""
    done: list[bool] = []

    def hook(env: dict, reply) -> None:
        if not done and env["type"] == "demo.clock" and env["data"]["clock"] == clock:
            done.append(True)
            action(reply)
    return hook


def types(msgs: list[dict]) -> list[str]:
    return [m["type"] for m in msgs]


def index_of(msgs: list[dict], pred) -> int:
    return next(i for i, m in enumerate(msgs) if pred(m))


def first_clock(clock: str):
    return lambda m: m["type"] == "demo.clock" and m["data"]["clock"] == clock


def of_type(kind: str):
    return lambda m: m["type"] == kind


def test_window_settles_after_jump():
    session = DemoSession(TL, "ARS")
    msgs = run_with(session, at_clock("01:28", lambda reply: reply(
        session.forecast_pick("f15", "goal"))))
    locked = index_of(msgs, of_type("forecast.locked"))
    assert msgs[locked]["data"] == {"key": "f15", "pick": "goal",
                                    "from_clock": "01:28", "to_clock": "16:28"}
    res_i = index_of(msgs, of_type("forecast.result"))
    assert msgs[res_i]["data"] == {
        "key": "f15", "pick": "goal", "outcome": "nogoal", "correct": False, "points": 0,
        "total": msgs[res_i]["data"]["total"], "to_clock": "16:28", "text": "Not this time"}
    jump = index_of(msgs, lambda m: m["type"] == "demo.jump"
                    and m["data"]["title"] == "Saka's goal")
    assert jump < res_i and res_i + 1 == index_of(msgs, first_clock("23:20"))
    assert types(msgs).count("forecast.result") == 1


def test_hit_adds_to_shared_total():
    session = DemoSession(TL, "ARS")
    pick = at_clock("23:20", lambda reply: reply(session.forecast_pick("f10", "ars")))

    def hook(env: dict, reply) -> None:
        if env["type"] == "call.open":  # every call right, so the call total is not zero
            session.answer(env["data"]["call_id"], TL.calls[env["data"]["call_id"]].answer)
        pick(env, reply)

    msgs = run_with(session, hook)
    res_i = index_of(msgs, of_type("forecast.result"))
    res = msgs[res_i]["data"]
    assert res["key"] == "f10" and res["outcome"] == "ars" and res["correct"] is True
    assert res["points"] == 1000 and res["text"] == "Forecast hit" and res["to_clock"] == "33:20"
    calls_before = [m["data"] for m in msgs[:res_i] if m["type"] == "call.result"]
    assert res["total"] == calls_before[-1]["total"] + 1000
    after = next(m["data"] for m in msgs[res_i:] if m["type"] == "call.result")
    assert after["total"] == res["total"] + after["points"]
    assert session.total == sum(m["data"]["points"]
                                for m in msgs if m["type"] == "call.result") + 1000


def test_pending_settles_at_half_time():
    session = DemoSession(TL, "ARS")
    msgs = run_with(session, at_clock("45+2:28", lambda reply: reply(
        session.forecast_pick("f5", "no"))))
    locked = next(m["data"] for m in msgs if m["type"] == "forecast.locked")
    assert locked == {"key": "f5", "pick": "no", "from_clock": "45+2:28", "to_clock": "45+2:36"}
    res_i = index_of(msgs, of_type("forecast.result"))
    assert msgs[res_i + 1]["type"] == "demo.half_time"
    res = msgs[res_i]["data"]
    assert res["outcome"] == "no" and res["correct"] is True and res["points"] == 500
    assert res["text"] == "Forecast hit"


def test_final_locked_in_second_half():
    session = DemoSession(TL, "CHE")
    replies: dict[str, dict] = {}

    def hook(env: dict, reply) -> None:
        if env["type"] != "demo.clock":
            return
        clock = env["data"]["clock"]
        if clock == "00:03" and "early" not in replies:
            replies["early"] = session.forecast_final("che", 0, 1)
            reply(replies["early"])
            replies["saved"] = session.forecast_final("ars", 2, 1)  # change of mind
            reply(replies["saved"])
        if clock == "59:55" and "late" not in replies:
            replies["late"] = session.forecast_final("draw", 1, 1)
            reply(replies["late"])

    msgs = run_with(session, hook)
    assert replies["early"] == {"type": "forecast.final_saved",
                                "data": {"result": "che", "home": 0, "away": 1}}
    assert replies["saved"] == {"type": "forecast.final_saved",
                                "data": {"result": "ars", "home": 2, "away": 1}}
    assert replies["late"] == {"type": "forecast.rejected",
                               "data": {"key": "final", "reason": "locked"}}
    lock_i = index_of(msgs, of_type("forecast.final_locked"))
    assert msgs[lock_i]["data"] == {"result": "ars", "home": 2, "away": 1}
    assert msgs[lock_i + 1] == next(m for m in msgs if m["type"] == "demo.clock"
                                    and m["data"]["half"] == 2)
    assert types(msgs).count("forecast.final_locked") == 1
    assert msgs[-1]["type"] == "demo.full_time" and msgs[-2]["type"] == "forecast.final_result"
    fin = msgs[-2]["data"]
    calls = sum(m["data"]["points"]
                for m in msgs if m["type"] == "call.result")
    assert fin == {"result": "ars", "home": 2, "away": 1, "final": [2, 1],
                   "result_points": 3000, "exact_points": 2000, "points": 5000,
                   "total": calls + 5000}
    assert session.total == calls + 5000


def test_final_locked_empty_and_no_result_without_pick():
    msgs = run_with(DemoSession(TL, "ARS"), lambda env, reply: None)
    lock = next(m for m in msgs if m["type"] == "forecast.final_locked")
    assert lock["data"] == {}
    assert "forecast.final_result" not in types(msgs) and "forecast.result" not in types(msgs)


def test_bad_forecast_messages():
    session = DemoSession(TL, "ARS")
    got: dict[str, dict] = {}

    def rej(name: str, msg: dict) -> None:
        got[name] = msg

    before = session.forecast_pick("f5", "yes")  # nothing sent yet

    def hook(env: dict, reply) -> None:
        kind, data = env["type"], env["data"]
        if kind == "demo.clock" and data["clock"] == "00:01" and "key" not in got:
            rej("key", session.forecast_pick("f20", "yes"))
            rej("pick", session.forecast_pick("f5", "goal"))
            rej("nonstr", session.forecast_pick(5, None))
            rej("ok", session.forecast_pick("f5", "yes"))
            rej("pending", session.forecast_pick("f5", "no"))
            rej("mismatch", session.forecast_final("ars", 1, 1))
            rej("range", session.forecast_final("ars", 10, 0))
            rej("types", session.forecast_final("ars", "2", None))
            rej("bool", session.forecast_final("ars", True, False))
            rej("result", session.forecast_final("home", 1, 0))
            rej("unhashable", session.forecast_final(["ars"], 1, 0))
        if kind == "demo.clock" and data["clock"] == "23:56" and "below" not in got:
            assert data["score"] == [1, 0]
            rej("below", session.forecast_final("draw", 0, 0))
            rej("level", session.forecast_final("ars", 1, 0))
        if kind == "demo.jump" and "jump" not in got:
            rej("jump", session.forecast_pick("f10", "ars"))
        if kind == "demo.half_time":
            rej("ht", session.forecast_pick("f10", "ars"))
            rej("ht_final", session.forecast_final("che", 1, 2))
        if kind == "demo.full_time":
            rej("ft", session.forecast_pick("f15", "goal"))

    run_with(session, hook)

    def rejected(key, reason) -> dict:
        return {"type": "forecast.rejected", "data": {"key": key, "reason": reason}}

    assert before == rejected("f5", "not_live")
    assert got["key"] == rejected("f20", "bad_pick")
    assert got["pick"] == rejected("f5", "bad_pick")
    assert got["nonstr"] == rejected(None, "bad_pick")
    assert got["ok"]["type"] == "forecast.locked"
    assert got["pending"] == rejected("f5", "pending")
    for name in ("mismatch", "range", "types", "bool", "result", "unhashable", "below"):
        assert got[name] == rejected("final", "bad_pick"), name
    assert got["level"]["type"] == "forecast.final_saved"
    assert got["jump"] == rejected("f10", "not_live")
    assert got["ht"] == rejected("f10", "not_live")
    assert got["ht_final"]["type"] == "forecast.final_saved"  # final stays open over the break
    assert got["ft"] == rejected("f15", "not_live")


def test_settled_key_can_be_picked_again():
    session = DemoSession(TL, "ARS")
    replies: list[dict] = []

    def hook(env: dict, reply) -> None:
        # the 00:00 window (0–300) settles just before the 23:20 tick, so f5 is free again
        if env["type"] == "demo.clock" and env["data"]["clock"] in ("00:00", "23:20"):
            replies.append(session.forecast_pick("f5", "yes"))
            reply(replies[-1])

    msgs = run_with(session, hook)
    assert [r["type"] for r in replies] == ["forecast.locked", "forecast.locked"]
    assert types(msgs).count("forecast.result") == 2


def test_all_settled_by_full_time():
    session = DemoSession(TL, "ARS")

    def pick_all(reply) -> None:
        for d in WINDOWS:
            reply(session.forecast_pick(d.key, d.options[0][0]))

    msgs = run_with(session, at_clock("90+4:15", pick_all))
    assert [m["data"]["to_clock"] for m in msgs if m["type"] == "forecast.locked"] == \
        ["90+4:27"] * 3
    ft = index_of(msgs, of_type("demo.full_time"))
    assert types(msgs).count("forecast.result") == 3
    tail = msgs[ft - 3:ft]
    assert [m["type"] for m in tail] == ["forecast.result"] * 3
    by_key = {m["data"]["key"]: m["data"] for m in tail}
    assert by_key["f5"]["outcome"] == "no" and by_key["f5"]["correct"] is False
    assert by_key["f10"]["outcome"] == "none" and by_key["f10"]["points"] == 0
    assert by_key["f10"]["text"] == "No shots in that spell. No points lost."
    assert by_key["f15"]["outcome"] == "nogoal" and by_key["f15"]["text"] == "Not this time"
    assert msgs[-1]["type"] == "demo.full_time"


def test_demo_clock_has_t():
    clocks = [c for c in TL.cues if c.type == "demo.clock"]
    assert clocks
    for c in clocks:
        assert set(c.data) == {"half", "clock", "score", "t"}
        assert isinstance(c.data["t"], float) and c.data["half"] in (1, 2)
    assert clocks[0].data == {"half": 1, "clock": "00:00", "score": [0, 0], "t": 0.0}
    assert TL.half_ends == {1: 2856.618, 2: 5667.866} and TL.final_score == (2, 1)
    assert TL.events


def test_session_message_lists_forecasts():
    msgs = run_with(DemoSession(TL, "ARS"), lambda env, reply: None)
    fc = msgs[0]["data"]["forecasts"]
    assert [w["key"] for w in fc["windows"]] == ["f5", "f10", "f15"]
    assert fc["windows"][1] == {
        "key": "f10", "label": "NEXT 10 MIN", "minutes": 10, "points": 1000,
        "question": "Who takes the next shot?",
        "options": [{"id": "ars", "label": "ARSENAL"}, {"id": "che", "label": "CHELSEA"}]}
    assert fc["final"] == {"points": 3000, "exact_points": 2000, "options": [
        {"id": "ars", "label": "ARSENAL"}, {"id": "draw", "label": "DRAW"},
        {"id": "che", "label": "CHELSEA"}]}


def test_router_routes_forecast_messages():
    router = make_demo_router(sleep=stall)
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client, client.websocket_connect("/demo/ws") as ws:
        ws.send_json(START_ARS)
        assert [ws.receive_json()["type"] for _ in range(3)] == \
            ["demo.session", "demo.clock", "demo.shape"]  # then the session stalls at 00:00
        ws.send_json({"type": "forecast.pick", "data": "nope"})
        assert ws.receive_json() == {"type": "forecast.rejected",
                                     "data": {"key": None, "reason": "bad_pick"}}
        ws.send_json({"type": "forecast.pick", "data": {"key": "f5", "pick": "yes"}})
        assert ws.receive_json() == {"type": "forecast.locked", "data": {
            "key": "f5", "pick": "yes", "from_clock": "00:00", "to_clock": "05:00"}}
        ws.send_json({"type": "forecast.final", "data": {"result": "ars", "home": 2, "away": 1}})
        assert ws.receive_json() == {"type": "forecast.final_saved",
                                     "data": {"result": "ars", "home": 2, "away": 1}}
        ws.send_json({"type": "forecast.final", "data": []})
        assert ws.receive_json() == {"type": "forecast.rejected",
                                     "data": {"key": "final", "reason": "bad_pick"}}
