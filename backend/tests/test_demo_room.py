"""test_demo_room.py — the demo room: friends, crowd, badges, stamp, signup and post-match socket.

Session behaviour is driven on DemoSession directly with an instant sleep; the router tests use a
TestClient websocket with a short injected POST_MATCH_S.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import random
import re

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import backend.demo.router as router_module
from backend.demo import room
from backend.demo.forecasts import WINDOWS, outcome, window
from backend.demo.router import make_demo_router
from backend.demo.script import build_timeline
from backend.demo.session import DemoSession
from backend.ingest.loader import load_match

TL = build_timeline(load_match("nbr-cal-seed42"))
START_ARS = {"type": "start", "data": {"team": "ARS"}}
EMAIL = "fan.secret+demo@example.org"
DEFS = {d.key: d for d in WINDOWS}


async def instant(_: float) -> None:
    await asyncio.sleep(0)


async def tick(delay: float) -> None:
    await asyncio.sleep(0.0005 if delay else 0)


def run(session: DemoSession, hook=None) -> list[dict]:
    """Run with an instant sleep; ``hook(env, reply)`` may call session methods like the router."""
    sent: list[dict] = []

    async def send(env: dict) -> None:
        sent.append(env)
        if hook:
            hook(env, sent.append)

    asyncio.run(session.run(send, instant))
    return sent


def perfect(session: DemoSession):
    """Every call right; at every tick, each free window picked with its true outcome; ARS 2-1."""
    def hook(env: dict, reply) -> None:
        kind, data = env["type"], env["data"]
        if kind == "call.open":
            session.answer(data["call_id"], TL.calls[data["call_id"]].answer)
        if kind == "demo.clock":
            if data["half"] == 1 and session._final is None:
                reply(session.forecast_final("ars", 2, 1))
            for key, defn in DEFS.items():
                t0, t1 = window(defn, data["half"], data["t"], TL.half_ends[data["half"]])
                got = outcome(TL.events, key, data["half"], t0, t1)
                if got in dict(defn.options):
                    reply(session.forecast_pick(key, got))
    return hook


def shooter(session: DemoSession):
    def hook(env: dict, reply) -> None:
        if env["type"] == "call.open":
            session.answer(env["data"]["call_id"], "SHOOT")
    return hook


def of(msgs: list[dict], kind: str) -> dict:
    return next(m["data"] for m in msgs if m["type"] == kind)


# --- friends and crowd ---------------------------------------------------------


def test_friends_deterministic_exact_totals():
    rows = room.friend_totals(TL)
    assert rows == room.friend_totals(TL)
    assert rows == [{"name": "Mia", "ht": 1890, "ft": 5010, "saved": True},
                    {"name": "Dan", "ht": 40, "ft": 3060, "saved": True},
                    {"name": "Leo", "ht": 390, "ft": 1910, "saved": False}]


def test_f15_one_pending_window():
    """Plays 2 and 3 start (16:51, 23:48) inside Play 1's f15 window (11:34-26:34): no pick there;
    nor does Play 5 (73:42) inside Play 4's (60:08-75:08)."""
    assert [(h, t) for h, t, _ in room._f15_outcomes(TL)] == [(1, 694.0), (2, 3608.0),
                                                               (2, 4784.0)]
    assert len(room.play_starts(TL)) == 6


def test_friend_progress_matches_the_call_part_of_the_totals():
    prog = room.friend_progress(TL)
    assert [f["name"] for f in prog] == ["Mia", "Dan", "Leo"]
    order = [c.call_id for c in sorted(TL.calls.values(), key=lambda c: (c.half, c.open_t))]
    for f in prog:
        assert list(f["calls"]) == order
        pts = list(f["calls"].values())
        assert pts == sorted(pts) and pts[0] in (10, 110)  # every friend answers every call


def test_session_message_has_the_room():
    session = DemoSession(TL, "ARS", code="K7QX")
    data = run(session)[0]["data"]
    assert data["room"]["code"] == "K7QX"
    assert data["room"]["friends"] == room.friend_progress(TL)


def test_friend_pick_rule():
    call = next(iter(TL.calls.values()))
    rng = random.Random(f"Mia-{call.call_id}")
    hit = rng.random() < 0.70
    pick = call.answer if hit else rng.choice([o for o in ("PASS", "SHOOT", "DRIBBLE")
                                               if o != call.answer])
    again = random.Random(f"Mia-{call.call_id}")
    assert room._pick(again, 0.70, call.answer, ["PASS", "SHOOT", "DRIBBLE"]) == pick


def test_crowd_and_top_pct():
    crowd = room.crowd_totals(TL)
    assert len(crowd) == 1000 and crowd is room.crowd_totals(TL)  # cached
    assert room.top_pct(0, crowd) == 100
    assert room.top_pct(10**9, crowd) == 1
    assert room.top_pct(360, crowd) == math.ceil(100 * sum(t >= 360 for t in crowd) / 1000)


def test_room_code_and_email_rules():
    for _ in range(50):
        assert re.fullmatch(r"[ABCDEFGHJKLMNPQRSTUVWXYZ23456789]{4}", room.room_code())
    assert room.valid_email("a@b.co")
    for bad in ("", "a@b", "a b@c.de", "@b.co", "a@@b.co", None, 5, ["a@b.co"],
                "a" * 250 + "@b.co", "a@b.co\n", "a@b.co\nb@c.de"):
        assert not room.valid_email(bad), bad


def test_stamp_and_badges():
    assert room.stamp(1, "K7QX", 3) == "#1 IN ROOM K7QX · TOP 3% TONIGHT"
    assert room.badges(False, 2, None) == []
    assert room.badges(True, 3, "Saka") == [
        {"title": "ORACLE", "text": "Called the full-time result"},
        {"title": "ON FIRE", "text": "3 calls in a row"},
        {"title": "GOOD EYE", "text": "Your pick Saka scored"}]


def test_good_eye_is_a_backed_player_who_scored():
    saka = {"name": "Saka", "stats": [{"what": "Goal", "count": 1, "pts": 4}]}
    rice = {"name": "Rice", "stats": [{"what": "Played 60+ minutes", "count": 1, "pts": 2}]}
    assert room.good_eye([rice, saka]) == "Saka"
    assert room.good_eye([rice]) is None and room.good_eye([]) is None


def test_backing_saka_earns_good_eye():
    session = DemoSession(TL, "ARS", code="K7QX", players=["910109"])
    ft = of(run(session), "demo.full_time")
    assert {"title": "GOOD EYE", "text": "Your pick Saka scored"} in ft["badges"]
    session = DemoSession(TL, "ARS", code="K7QX", players=["910108"])  # Rice: no goal
    assert all(b["title"] != "GOOD EYE" for b in of(run(session), "demo.full_time")["badges"])


# --- half time and full time ---------------------------------------------------


def test_perfect_judge_is_first_with_all_badges():
    session = DemoSession(TL, "ARS", code="K7QX")
    msgs = run(session, perfect(session))
    ht, ft = of(msgs, "demo.half_time"), of(msgs, "demo.full_time")
    assert set(ht) == {"score", "seconds", "room", "breakdown", "you", "saved",
                       "players", "total"}
    assert ht["total"] == next(p for p in ht["room"]["players"] if p["me"])["pts"]
    assert ht["you"] == {"rank": 1} and ht["saved"] is False and ht["room"]["code"] == "K7QX"
    assert [p["name"] for p in ht["room"]["players"]] == ["You", "Mia", "Leo", "Dan"]  # Dan 40 < Leo 390 at HT
    assert ht["room"]["players"][0] == {"name": "You", "pts": ht["breakdown"]["calls"]
                                        + ht["breakdown"]["forecasts"], "saved": False, "me": True}
    assert ht["breakdown"]["calls"] == 4 * 110 + 50 * 3

    assert set(ft) == {"score", "seconds", "rows", "badges", "stamp", "room", "total", "saved",
                       "players"}
    assert [r["key"] for r in ft["rows"]] == ["rt", "fc", "pb"]
    assert ft["rows"][0] == {"key": "rt", "label": "REAL-TIME CALLS", "points": 1160,
                             "detail": "6 correct calls · best streak of 6"}
    assert ft["rows"][1]["label"] == "FORECAST"
    assert ft["rows"][1]["detail"].endswith(" forecasts hit · full-time result nailed")
    assert ft["rows"][2] == {"key": "pb", "label": "PLAYER BONUS",
                             "detail": "Back players next match", "points": 0}
    assert sum(r["points"] for r in ft["rows"]) == ft["total"] == session.total
    assert ft["badges"] == [{"title": "ORACLE", "text": "Called the full-time result"},
                            {"title": "ON FIRE", "text": "6 calls in a row"}]  # no players backed
    pct = room.top_pct(session.total, room.crowd_totals(TL))
    assert ft["stamp"] == f"#1 IN ROOM K7QX · TOP {pct}% TONIGHT"
    pts = [p["pts"] for p in ft["room"]["players"]]
    assert pts == sorted(pts, reverse=True) and ft["room"]["players"][0]["me"]
    assert TL.cues[-1].data == {"score": [2, 1], "seconds": 0}  # shared cue data untouched


def test_always_shoot_judge():
    session = DemoSession(TL, "ARS", code="K7QX")
    ft = of(run(session, shooter(session)), "demo.full_time")
    assert ft["total"] == 360
    assert ft["rows"][0]["detail"] == "3 correct calls · best streak of 2"
    assert ft["rows"][1] == {"key": "fc", "label": "FORECAST", "detail": "Forecasts open every match",
                             "points": 0}
    assert ft["badges"] == []  # no players backed, so no GOOD EYE
    assert ft["stamp"] == "#4 IN ROOM K7QX · TOP 94% TONIGHT"  # below Leo (1910) and Dan (3060)


def test_calls_and_f15_and_final_judge_leads():
    """Perfect calls, an f15 at each Play start (the pending one is rejected), final ARS 2-1."""
    session = DemoSession(TL, "ARS", code="K7QX")
    starts = set(room.play_starts(TL))

    def hook(env: dict, reply) -> None:
        kind, data = env["type"], env["data"]
        if kind == "call.open":
            session.answer(data["call_id"], TL.calls[data["call_id"]].answer)
        if kind == "demo.clock" and (data["half"], data["t"]) in starts:
            if session._final is None:
                reply(session.forecast_final("ars", 2, 1))
            reply(session.forecast_pick("f15", "goal"))

    msgs = run(session, hook)
    assert of(msgs, "demo.half_time")["you"] == {"rank": 1}
    ft = of(msgs, "demo.full_time")
    assert ft["total"] == 1160 + 3 * 1500 + 5000 == 10660
    assert ft["rows"][1]["detail"] == "4 forecasts hit · full-time result nailed"  # 3 windows + the result
    assert ft["stamp"] == "#1 IN ROOM K7QX · TOP 1% TONIGHT"


def test_fan_wins_ties_except_at_zero():
    friends = [{"name": "Leo", "ht": 240, "ft": 0, "saved": False}]
    assert [p["me"] for p in room.board(240, False, friends, "ht")] == [True, False]
    assert [p["me"] for p in room.board(0, False, friends, "ft")] == [False, True]


def test_silent_judge_is_last():
    session = DemoSession(TL, "CHE", code="AB23")
    msgs = run(session)
    ht, ft = of(msgs, "demo.half_time"), of(msgs, "demo.full_time")
    assert ht["you"] == {"rank": 4} and ht["breakdown"] == {"calls": 0, "forecasts": 0, "players": 0}
    assert ft["badges"] == [] and ft["total"] == 0
    assert ft["stamp"] == "#4 IN ROOM AB23 · TOP 100% TONIGHT"
    assert ft["room"]["players"][-1] == {"name": "You", "pts": 0, "saved": False, "me": True}


# --- signup --------------------------------------------------------------------


def test_signup_once():
    session = DemoSession(TL, "ARS")
    assert session.signup("nope") == {"type": "signup.rejected", "data": {"reason": "bad_email"}}
    assert session.signup(EMAIL) == {"type": "signup.saved", "data": {"bonus": 500, "total": 500}}
    assert session.signup(EMAIL) == {"type": "signup.rejected", "data": {"reason": "already"}}
    assert session.signup("other@x.io")["data"] == {"reason": "already"}
    assert session.total == 500 and session.saved
    assert EMAIL not in repr(vars(session))


def test_signup_before_half_time_shows_saved():
    session = DemoSession(TL, "ARS")
    session.signup(EMAIL)
    msgs = run(session)
    ht, ft = of(msgs, "demo.half_time"), of(msgs, "demo.full_time")
    assert ht["saved"] and ft["saved"] and ft["total"] == 500
    assert sum(r["points"] for r in ft["rows"]) == 0  # the bonus is not a row


# --- router --------------------------------------------------------------------


def client_for(**kw) -> tuple[TestClient, object]:
    router = make_demo_router(**kw)
    app = FastAPI()
    app.include_router(router)
    return TestClient(app), router


def until_full_time(ws) -> list[dict]:
    msgs = []
    while not msgs or msgs[-1]["type"] != "demo.full_time":
        msgs.append(ws.receive_json())
    return msgs


def test_signup_after_full_time_then_client_closes(monkeypatch, caplog):
    monkeypatch.setattr(router_module, "POST_MATCH_S", 30.0)
    caplog.set_level(logging.DEBUG)
    client, router = client_for(sleep=tick)
    with client:
        with client.websocket_connect("/demo/ws") as ws:
            ws.send_json(START_ARS)
            ws.send_json({"type": "signup", "data": {"email": "bad"}})
            msgs = until_full_time(ws)
            ws.send_json({"type": "signup", "data": {"email": EMAIL}})
            ws.send_json({"type": "signup", "data": {"email": EMAIL}})
            ws.send_json({"type": "signup", "data": "x"})
            after = [ws.receive_json() for _ in range(3)]
            assert len(router.active) == 1  # still open after full time
        assert wait_until(lambda: len(router.active) == 0)
    assert {"type": "signup.rejected", "data": {"reason": "bad_email"}} in msgs
    assert after == [{"type": "signup.saved", "data": {"bonus": 500, "total": 500}},
                     {"type": "signup.rejected", "data": {"reason": "already"}},
                     {"type": "signup.rejected", "data": {"reason": "already"}}]
    assert all(EMAIL not in json.dumps(m) for m in msgs + after)
    assert EMAIL not in caplog.text


def test_post_match_closes_after_timeout(monkeypatch):
    monkeypatch.setattr(router_module, "POST_MATCH_S", 0.1)
    client, router = client_for(sleep=tick)
    with client:
        with client.websocket_connect("/demo/ws") as ws:
            ws.send_json(START_ARS)
            until_full_time(ws)
            with pytest.raises(WebSocketDisconnect) as gone:
                ws.receive_json()
            assert gone.value.code == 1000
        assert wait_until(lambda: len(router.active) == 0)


def wait_until(cond, timeout: float = 3.0) -> bool:
    import time
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.01)
    return cond()
