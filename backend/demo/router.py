"""WS /demo/ws: one DemoSession per socket, beside a reader that takes the fan's calls.

Protocol: first message ``start`` {team: ARS|CHE} within 10 s, else close 1008. Then any
number of ``call.answer`` {call_id, pick}; bad input after start is ignored. The server
closes with 1000 after ``demo.full_time``. Sockets over ``max_sessions`` close with 1013.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any, Callable

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from backend.core.types import Match
from backend.demo.script import Timeline, build_timeline
from backend.demo.session import DemoSession, Sleep

DEMO_MATCH_ID = "nbr-cal-seed42"
MAX_SESSIONS_ENV = "MAX_DEMO_SESSIONS"
DEFAULT_MAX_SESSIONS = 50
START_TIMEOUT_S = 10.0
MAX_FRAME_BYTES = 4096
TEAM_CODES = frozenset({"ARS", "CHE"})
WS_CLOSE_NORMAL = 1000
WS_CLOSE_POLICY = 1008
WS_CLOSE_TRY_AGAIN = 1013
GONE = (WebSocketDisconnect, RuntimeError, OSError)  # the client went away mid-send


def _load_default() -> Match:
    from backend.ingest.loader import load_match  # lazy: keeps app import light
    return load_match(DEMO_MATCH_ID)


async def _recv(ws: WebSocket) -> Any:
    """Next client frame as JSON. Raises WebSocketDisconnect, or ValueError if not JSON."""
    msg = await ws.receive()
    if msg["type"] == "websocket.disconnect":
        raise WebSocketDisconnect(msg.get("code", WS_CLOSE_NORMAL))
    raw = msg.get("text")
    if raw is None:
        raw = msg.get("bytes") or b""
    if len(raw) > MAX_FRAME_BYTES:
        raise ValueError("frame too large")
    return json.loads(raw)


async def _safe_close(ws: WebSocket, code: int) -> None:
    try:
        await ws.close(code)
    except GONE:
        pass  # already closed


def make_demo_router(*, sleep: Sleep = asyncio.sleep, load: Callable[[], Match] = _load_default,
                     max_sessions: int | None = None) -> APIRouter:
    """Router serving WS /demo/ws. ``router.active`` holds one slot per open demo socket."""
    cap = max_sessions if max_sessions is not None else int(
        os.environ.get(MAX_SESSIONS_ENV, DEFAULT_MAX_SESSIONS))
    router = APIRouter()
    active: set[object] = set()
    router.active = active  # type: ignore[attr-defined]  # exposed for tests and diagnostics
    cache: dict[str, Timeline] = {}

    async def timeline() -> Timeline:
        """Built once, on first connect, off the event loop."""
        if "tl" not in cache:
            cache["tl"] = await asyncio.to_thread(lambda: build_timeline(load()))
        return cache["tl"]

    async def read_start(ws: WebSocket) -> str | None:
        """Team code from a valid ``start`` within START_TIMEOUT_S, else close 1008."""
        try:
            first = await asyncio.wait_for(_recv(ws), START_TIMEOUT_S)
            team = first["data"]["team"] if first.get("type") == "start" else None
            if isinstance(team, str) and team in TEAM_CODES:
                return team
        except WebSocketDisconnect:
            return None
        except (asyncio.TimeoutError, ValueError, KeyError, TypeError, AttributeError):
            pass
        await _safe_close(ws, WS_CLOSE_POLICY)
        return None

    async def serve(ws: WebSocket) -> None:
        team = await read_start(ws)
        if team is None:
            return
        session = DemoSession(await timeline(), team)

        async def send(env: dict) -> None:
            await ws.send_json(env)

        async def play() -> bool:
            """True when the demo reached full time; False when the client left."""
            try:
                await session.run(send, sleep)
            except GONE:
                return False
            return True

        async def reader() -> None:
            """Route ``call.answer``; ignore anything malformed; return when the client leaves."""
            while True:
                try:
                    msg = await _recv(ws)
                    if msg.get("type") == "call.answer":
                        data = msg.get("data") or {}
                        session.answer(data.get("call_id"), data.get("pick"))
                except (ValueError, KeyError, TypeError, AttributeError):
                    continue
                except GONE:
                    return

        play_task = asyncio.create_task(play())
        reader_task = asyncio.create_task(reader())

        async def cleanup() -> None:
            for t in (play_task, reader_task):
                t.cancel()
            await asyncio.gather(play_task, reader_task, return_exceptions=True)
            await _safe_close(ws, WS_CLOSE_NORMAL)

        try:
            await asyncio.wait({play_task, reader_task}, return_when=asyncio.FIRST_COMPLETED)
        except asyncio.CancelledError:
            pass  # server shutdown; clean up below
        finally:
            job = asyncio.ensure_future(cleanup())
            try:
                await asyncio.shield(job)  # cleanup finishes even if this handler is cancelled
            except asyncio.CancelledError:
                pass

    @router.websocket("/demo/ws")
    async def demo_ws(websocket: WebSocket) -> None:
        await websocket.accept()
        if len(active) >= cap:
            await _safe_close(websocket, WS_CLOSE_TRY_AGAIN)
            return
        slot = object()
        active.add(slot)
        try:
            await serve(websocket)
        finally:
            active.discard(slot)

    return router
