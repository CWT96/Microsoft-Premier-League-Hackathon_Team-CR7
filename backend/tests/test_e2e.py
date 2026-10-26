"""test_e2e.py — the real dataset through /ws at max speed, lobby to score.final.

Last Modified: 2026-10-24 23:59, first version
Inputs:  data/source/ demo match (via backend.ingest), backend.detect, backend.model
Outputs: data/generated/e2e_log.jsonl (every server message)

Workflow:
  - a scripted client joins, answers every card, and resumes each pause after a few repeated
    states so the stacks and break cards are seen
  - assertions target behaviour (stacks, calm cards, full time, validity),
    never exact counts, because the dataset can be regenerated

CAUTION: skipped when ingest, detect or model cannot be imported or the dataset is missing.
"""

from __future__ import annotations

import asyncio
import importlib
import json
from pathlib import Path

import pytest

from backend.tests.contract_utils import validate

ROOT = Path(__file__).resolve().parents[2]
LOG_PATH = ROOT / "data" / "generated" / "e2e_log.jsonl"
MAX_MESSAGES = 400_000
REPEATS_BEFORE_RESUME = 3  # paused states seen before the client taps resume
SCHEMAS = {"question.open": "question", "question.result": "result", "score.final": "score",
           "match.state": "state", "match.tracking": "tracking", "match.event": "event",
           "timing.decision": "timing_decision", "nudge": "nudge"}


def _available() -> bool:
    try:
        for name in ("backend.ingest", "backend.detect.detector", "backend.model"):
            importlib.import_module(name)
        from backend.core.constants import DEFAULT_MATCH_ID
        from backend.ingest import load_match
        load_match(DEFAULT_MATCH_ID)
    except (ImportError, FileNotFoundError):
        return False
    return True


# Full-match run over a real WebSocket (~3 min): run with `pytest -m slow`.
pytestmark = [pytest.mark.slow,
              pytest.mark.skipif(not _available(), reason="ingest/detect/model/dataset missing")]


FAST_RATE = (10_000.0, 10_000.0)  # the scripted clients answer faster than a human
MIN_SLEEP_S = 0.002  # a browser at 15x gets a tick every 67 ms; stay well above the flood rate
SLEEP_SCALE = 0.005  # real delay per replay second; long enough for a client round trip


async def scaled_sleep(delay: float) -> None:
    await asyncio.sleep(max(delay * SLEEP_SCALE, MIN_SLEEP_S))


@pytest.fixture(scope="module")
def log() -> list[dict]:
    from fastapi.testclient import TestClient

    from backend.app.main import create_app
    app = create_app(sleep=scaled_sleep, speed=15, rate_limit=FAST_RATE)
    out: list[dict] = []
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "join", "data": {"display_name": "Judge", "team": "NBR",
                                               "player_id": None, "room_id": None}})
        paused_key, repeats = None, 0
        for _ in range(MAX_MESSAGES):
            msg = ws.receive_json()
            out.append(msg)
            data = msg["data"]
            if msg["type"] == "question.open":
                ws.send_json({"type": "answer", "data": {
                    "qid": data["qid"], "option": data["options"][0]["id"]}})
            elif msg["type"] == "match.state":
                key = (data["mode"], data["half"], data["t"])
                paused = data["mode"] in ("lobby", "break")
                repeats = repeats + 1 if paused and key == paused_key else 0
                paused_key = key if paused else None
                if paused and repeats >= REPEATS_BEFORE_RESUME:
                    ws.send_json({"type": "control", "data": {"action": "resume", "value": None}})
                    repeats = 0
            elif msg["type"] == "leaderboard" and data["scope"] == "global":
                break
        else:
            pytest.fail("no full time within the message cap")
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOG_PATH.write_text("\n".join(json.dumps(m) for m in out) + "\n")
    return out


def opens(log: list[dict]) -> list[dict]:
    return [m["data"] for m in log if m["type"] == "question.open"]


def test_every_message_validates(log):
    for m in log:
        if m["type"] in SCHEMAS:
            validate(SCHEMAS[m["type"]], m["data"])


def test_kickoff_stack_comes_before_the_first_live_second(log):
    first_live = next(i for i, m in enumerate(log)
                      if m["type"] == "match.state" and m["data"]["mode"] in ("live", "game"))
    stack = [m["data"] for m in log[:first_live] if m["type"] == "question.open"]
    assert stack and all(q["lock_at"] - q["open_at"] == 60 for q in stack)
    assert log[0]["type"] == "session"


def test_calm_play_and_half_time_cards(log):
    qs = opens(log)
    assert any(q["situation"] == "calm_play" and q["category"] == "instant" for q in qs)
    assert any(q["situation"] == "half_time" for q in qs)


def test_no_card_opens_during_an_attack(log):
    pending = []
    for m in log:
        if m["type"] == "question.open":
            pending.append(m)
        elif m["type"] == "match.state":
            assert m["data"]["mode"] != "attack" or not pending, "card opened in an attack"
            pending = []


def test_full_time_score_and_boards(log):
    finals = [m["data"] for m in log if m["type"] == "score.final"]
    assert len(finals) == 1 and finals[0]["answered"] > 0
    assert {m["data"]["scope"] for m in log if m["type"] == "leaderboard"} == {
        "room", "friends", "global"}
    assert [m for m in log if m["type"] == "catchup.card"]


# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
#
# Skip "next" on the dataset: scheduled stops only, in match order
#
# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

SKIP_LEAD_MAX_S = 6  # landing is 3 s before the stop; allow a little slack for the tick
# (half, earliest t, latest t) of each landing: player slot, half time, player slot, full time.
# The dataset has no water breaks. Windows are wide so a regenerated dataset still fits.
EXPECTED_STOPS = ((1, 2380, 2500), (1, 2600, 3000), (2, 4190, 4400), (2, 5300, 6000))


def test_successive_skips_visit_scheduled_stops_in_order():
    from backend.app.replay import Room, build_bundle
    clock = [0.0]
    room = Room("r-skip", build_bundle(), now=lambda: clock[0], speed=15)
    uid = "u0"
    room.add_member(uid, "Judge", "NBR", None, lambda m: None)
    landings = []

    def advance() -> float | None:
        delay = room.tick()
        if delay is not None:
            clock[0] += delay
        return delay

    advance()  # lobby
    room.control(uid, "resume")
    for _ in EXPECTED_STOPS:
        room.control(uid, "skip", "next")
        if advance() is None:
            break
        landings.append((room.state.half, room.state.t))
        # play through the stop itself before pressing next again
        while advance() is not None and room.state.t < landings[-1][1] + SKIP_LEAD_MAX_S + 3:
            if room.pause is not None:
                room.control(uid, "resume")
        if room.done:
            break
    assert len(landings) == len(EXPECTED_STOPS), landings
    for (half, t), (want_half, lo, hi) in zip(landings, EXPECTED_STOPS):
        assert half == want_half and lo <= t <= hi, (half, t, want_half, lo, hi)


# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
#
# Several humans in one room, and the skip sequence, through real WebSockets
#
# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%


def _send_resume(ws) -> None:
    ws.send_json({"type": "control", "data": {"action": "resume", "value": None}})


def _client(client, room: str, player_id: str | None, role: str, joined: dict,
            out: list, errors: list) -> None:
    """One scripted human. Roles: host (late answer + resumes), plain (answers all), idle."""
    import threading
    try:
        with client.websocket_connect(f"/ws?room_id={room}") as ws:
            ws.send_json({"type": "join", "data": {"display_name": role, "player_id": player_id}})
            joined[role].set()
            skipped_qid = late = None
            paused_key, repeats, started = None, 0, False
            for _ in range(MAX_MESSAGES):
                msg = ws.receive_json()
                out.append(msg)
                data = msg["data"]
                kind = msg["type"]
                if kind == "question.open" and role != "idle":
                    if role == "host" and skipped_qid is None and data["category"] == "instant":
                        skipped_qid = data["qid"]  # leave this one alone, answer it late
                    else:
                        ws.send_json({"type": "answer", "data": {
                            "qid": data["qid"], "option": data["options"][0]["id"]}})
                elif kind == "question.locked" and data["qid"] == skipped_qid and late is None:
                    late = True
                    ws.send_json({"type": "answer", "data": {
                        "qid": skipped_qid, "option": "yes"}})
                elif kind == "nudge" and role == "idle":
                    ws.send_json({"type": "feed.resume", "data": {}})
                elif kind == "match.state" and role == "host":
                    key = (data["mode"], data["half"], data["t"])
                    paused = data["mode"] in ("lobby", "break")
                    repeats = repeats + 1 if paused and key == paused_key else 0
                    paused_key = key if paused else None
                    if paused and repeats >= REPEATS_BEFORE_RESUME:
                        if not started:
                            for ev in joined.values():  # wait for the other humans
                                ev.wait(timeout=30)
                            started = True
                        _send_resume(ws)
                        repeats = 0
                elif kind == "leaderboard" and data["scope"] == "global":
                    break
    except Exception as exc:  # noqa: BLE001 - surface thread failures in the test
        errors.append(repr(exc))


@pytest.fixture(scope="module")
def room_logs() -> dict[str, list[dict]]:
    import threading

    from fastapi.testclient import TestClient

    from backend.app.main import create_app
    app = create_app(sleep=scaled_sleep, speed=15, rate_limit=FAST_RATE)
    logs = {"host": [], "plain": [], "idle": []}
    errors: list[str] = []
    with TestClient(app) as client:
        roster = client.get("/api/matches/nbr-cal-seed42/players").json()
        home = [p["player_id"] for p in roster if p["position"] == "GK"]
        pids = {"host": home[0], "plain": home[1], "idle": None}
        room = client.post("/api/rooms").json()["room_id"]
        joined = {role: threading.Event() for role in logs}
        threads = [threading.Thread(target=_client, args=(
            client, room, pids[role], role, joined, logs[role], errors)) for role in logs]
        for t in threads:
            t.start()
            time.sleep(0.2)  # the host joins first
        for t in threads:
            t.join(timeout=300)
    assert not errors, errors
    logs["_pids"] = pids  # type: ignore[assignment]
    return logs


import time  # noqa: E402  (used by the fixture above)


def test_player_cards_use_a_per_user_qid(room_logs):
    pids = room_logs["_pids"]
    mine = {}
    for role in ("host", "plain"):
        player_cards = [m["data"] for m in room_logs[role]
                        if m["type"] == "question.open" and m["data"]["category"] == "player"]
        assert player_cards, role
        assert all(q["qid"].endswith(f"-{pids[role]}") and q["player_id"] == pids[role]
                   for q in player_cards)
        mine[role] = {q["qid"] for q in player_cards}
    assert not mine["host"] & mine["plain"]
    shared = lambda role: {m["data"]["qid"] for m in room_logs[role]  # noqa: E731
                           if m["type"] == "question.open" and m["data"]["category"] != "player"}
    assert shared("host") & shared("plain")  # same moment, same qid


def test_idle_human_gets_a_nudge_then_cards_after_feed_resume(room_logs):
    log = room_logs["idle"]
    types = [m["type"] for m in log]
    assert "nudge" in types
    first_nudge = types.index("nudge")
    assert "question.open" in types[first_nudge + 1:]
    assert all(m["data"]["you"]["answered"] is False
               for m in log if m["type"] == "question.result")


def test_a_late_answer_is_rejected(room_logs):
    log = room_logs["host"]
    opens = [m["data"]["qid"] for m in log if m["type"] == "question.open"
             and m["data"]["category"] == "instant"]
    skipped = opens[0]
    result = next(m["data"] for m in log if m["type"] == "question.result"
                  and m["data"]["qid"] == skipped)
    assert result["you"]["answered"] is False and result["you"]["option"] is None


def test_all_three_humans_reach_full_time_and_everything_validates(room_logs):
    for role in ("host", "plain", "idle"):
        assert len([m for m in room_logs[role] if m["type"] == "score.final"]) == 1
        for m in room_logs[role]:
            if m["type"] in SCHEMAS:
                validate(SCHEMAS[m["type"]], m["data"])


def test_skip_sequence_over_the_websocket():
    from fastapi.testclient import TestClient

    from backend.app.main import create_app
    app = create_app(sleep=scaled_sleep, speed=15, rate_limit=FAST_RATE)
    landings: list[tuple[int, float]] = []
    prev = None
    awaiting = False
    last_landing_t = 0.0
    paused_key, repeats = None, 0
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "join", "data": {"display_name": "Skipper"}})
        for _ in range(MAX_MESSAGES):
            msg = ws.receive_json()
            data = msg["data"]
            if msg["type"] == "leaderboard" and data["scope"] == "global":
                break
            if msg["type"] != "match.state":
                continue
            key = (data["mode"], data["half"], data["t"])
            paused = data["mode"] in ("lobby", "break")
            repeats = repeats + 1 if paused and key == paused_key else 0
            paused_key = key if paused else None
            if paused and repeats >= REPEATS_BEFORE_RESUME:
                _send_resume(ws)
                repeats = 0
            if prev and awaiting and data["half"] == prev[0] and data["t"] - prev[1] > 10:
                landings.append((data["half"], data["t"]))
                last_landing_t, awaiting = data["t"], False
            prev = (data["half"], data["t"])
            if not awaiting and not paused and data["mode"] != "full_time" \
                    and data["t"] >= last_landing_t + 8 and len(landings) < len(EXPECTED_STOPS):
                ws.send_json({"type": "control", "data": {"action": "skip", "value": "next"}})
                awaiting = True
    assert len(landings) == len(EXPECTED_STOPS), landings
    for (half, t), (want_half, lo, hi) in zip(landings, EXPECTED_STOPS):
        assert half == want_half and lo <= t <= hi, (half, t, want_half, lo, hi)
