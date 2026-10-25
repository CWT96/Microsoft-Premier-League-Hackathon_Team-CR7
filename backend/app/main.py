"""main.py — FastAPI app: /ws replay sessions and the REST endpoints of contracts/README.md.

Last Modified: 2026-10-25 00:40, review round 1: room supervision, back-pressure, worker threads
Inputs:  MatchSource (default: ingest + detector + model, loaded lazily); env REPLAY_SPEED,
         CORS_ORIGINS (comma list, default "*"), MAX_ROOMS (default 50), PUBLIC_BASE_URL
Outputs: ``app`` (uvicorn backend.app.main:app); in-memory rooms, scores and sign-ups

Workflow:
  - /ws: first message ``join`` (10 s) -> ``session``; ``answer``, ``hint.request``,
    ``control`` (host only) and ``feed.resume`` are routed to the user's Room
  - one Room per room_id (solo players get a private room). Room.tick runs in a worker thread;
    its messages reach each socket through a bounded Outbox via call_soon_threadsafe
  - a finished or crashed room is dropped and its sockets closed; a rejoin gets a fresh room
  - REST: matches, players, rooms, signup (mock, memory only), card, score, ops funnel,
    /healthz (always), /readyz (warm-up done)
  - warm-up (llm client, ingest, detector, model) runs in the background and is non-fatal

CAUTION: rooms, scores and sign-ups live in this process's memory, so the app MUST run with a
         single worker (uvicorn --workers 1). A restart restarts every match.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import threading
import time
import uuid
from collections import deque
from contextlib import asynccontextmanager
from typing import Any, Callable, Protocol

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError, field_validator

from backend.agents import llm
from backend.app.replay import APP_SEED, MatchBundle, Room, Sleep, build_bundle
from backend.core.constants import DEFAULT_MATCH_ID
from backend.scoring.scoring import tier_for
from backend.social.funnel import ops_funnel

logger = logging.getLogger(__name__)

KICKOFF_LABEL = "Tonight, 8:00 pm"
PUBLIC_BASE_ENV = "PUBLIC_BASE_URL"
CORS_ENV = "CORS_ORIGINS"
MAX_ROOMS_ENV = "MAX_ROOMS"
DEFAULT_MAX_ROOMS = 50
JOIN_TIMEOUT_S = 10.0
OUTBOX_MAX = 300  # queued messages per socket before it is considered too slow
STALE_TYPES = frozenset({"match.state", "match.tracking"})  # dropped first when a queue is full
ROOM_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
DISPLAY_NAME_MAX = 24
EMAIL_MAX = 254
EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"
WS_CLOSE_NORMAL = 1000
WS_CLOSE_TRY_AGAIN = 1013
WS_CLOSE_BAD_JOIN = 4400
WS_CLOSE_UNKNOWN_MATCH = 4404
WS_CLOSE_JOIN_TIMEOUT = 4408
WS_CLOSE_BUSY = 4429


class MatchSource(Protocol):
    def match_ids(self) -> list[str]: ...
    def get(self, match_id: str) -> MatchBundle: ...


class DefaultSource:
    """Loads each match once (ingest, detector, model) and keeps the bundle."""

    def __init__(self, builder: Callable[[str], MatchBundle] = build_bundle) -> None:
        self._builder = builder
        self._cache: dict[str, MatchBundle] = {}
        self._lock = threading.Lock()

    def match_ids(self) -> list[str]:
        return [DEFAULT_MATCH_ID]

    def get(self, match_id: str) -> MatchBundle:
        if match_id not in self.match_ids():
            raise KeyError(match_id)
        with self._lock:  # called from worker threads; build each match once
            if match_id not in self._cache:
                self._cache[match_id] = self._builder(match_id)
            return self._cache[match_id]


class Join(BaseModel):
    match_id: str | None = None
    room_id: str | None = None
    display_name: str = "Fan"
    team: str | None = None
    player_id: str | None = None

    @field_validator("display_name", mode="before")
    @classmethod
    def _clean_name(cls, v: Any) -> str:
        name = str(v).strip()[:DISPLAY_NAME_MAX]
        return name or "Fan"


class Signup(BaseModel):
    user_id: str = Field(max_length=64)
    email: str = Field(max_length=EMAIL_MAX, pattern=EMAIL_PATTERN)


class Outbox:
    """Bounded per-socket message queue; lives on the event loop thread."""

    def __init__(self, maxsize: int = OUTBOX_MAX) -> None:
        self._items: deque[dict[str, Any]] = deque()
        self._max = maxsize
        self._wake = asyncio.Event()
        self.overflowed = False
        self.closed = False

    def push(self, env: dict[str, Any]) -> None:
        if self.overflowed or self.closed:
            return
        if len(self._items) >= self._max:  # drop stale positions before giving up
            self._items = deque(e for e in self._items if e["type"] not in STALE_TYPES)
        if len(self._items) >= self._max:
            self.overflowed = True
        else:
            self._items.append(env)
        self._wake.set()

    def close(self) -> None:
        """No more messages; the writer drains what is queued, then stops."""
        self.closed = True
        self._wake.set()

    async def get(self) -> dict[str, Any] | None:
        """Next message, or None when the socket should be closed."""
        while True:
            if self.overflowed:
                return None
            if self._items:
                return self._items.popleft()
            if self.closed:
                return None
            self._wake.clear()
            await self._wake.wait()


def _tier(score: int) -> str:
    return tier_for(score)


async def _safe_close(ws: WebSocket, code: int) -> None:
    try:
        await ws.close(code)
    except RuntimeError:
        pass  # already closed


def create_app(source: MatchSource | None = None, *, sleep: Sleep = asyncio.sleep,
               now: Callable[[], float] = time.monotonic, speed: float | None = None,
               propose: Callable | None = None, max_rooms: int | None = None) -> FastAPI:
    """Build the app. ``sleep``, ``now``, ``speed`` and ``propose`` are injectable for tests."""
    src: MatchSource = source or DefaultSource()
    room_cap = max_rooms if max_rooms is not None else int(
        os.environ.get(MAX_ROOMS_ENV, DEFAULT_MAX_ROOMS))
    rooms: dict[str, Room] = {}
    tasks: dict[str, asyncio.Task] = {}
    conns: dict[str, dict[str, Outbox]] = {}
    scores: dict[str, dict[str, Any]] = {}
    names: dict[str, str] = {}
    members: dict[str, dict[str, str]] = {}  # member_id -> {user_id, email}
    warm = {"ready": False, "error": None}

    def warm_sync() -> None:
        llm.get_client()
        if source is None:
            src.get(DEFAULT_MATCH_ID)  # ingest, detector and model

    async def warm_up() -> None:
        try:
            await asyncio.to_thread(warm_sync)
        except Exception as exc:  # noqa: BLE001 - warm-up must never stop the app
            logger.exception("warm-up failed; matches load on first use")
            warm["error"] = repr(exc)
        finally:
            warm["ready"] = True

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        task = asyncio.create_task(warm_up())
        yield
        task.cancel()
        for t in list(tasks.values()):
            t.cancel()

    app = FastAPI(title="CR7 second screen", lifespan=lifespan)
    origins = [o.strip() for o in os.environ.get(CORS_ENV, "*").split(",") if o.strip()]
    app.add_middleware(CORSMiddleware, allow_origins=origins or ["*"], allow_credentials=False,
                       allow_methods=["*"], allow_headers=["*"])
    app.state.rooms = rooms  # exposed for tests and diagnostics

    async def load(match_id: str) -> MatchBundle:
        try:
            return await asyncio.to_thread(src.get, match_id)
        except KeyError:
            raise HTTPException(404, f"unknown match {match_id}") from None
        except (ImportError, FileNotFoundError) as exc:
            raise HTTPException(503, f"match data not available: {exc}") from None

    # .........................................................................
    # REST
    # .........................................................................

    @app.get("/healthz")
    async def healthz() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/readyz")
    async def readyz() -> JSONResponse:
        return JSONResponse({"ready": warm["ready"], "warmup_error": warm["error"]},
                            status_code=200 if warm["ready"] else 503)

    @app.get("/api/matches")
    async def matches() -> list[dict[str, str]]:
        out = []
        for mid in src.match_ids():
            r = (await load(mid)).match.roster
            out.append({"match_id": mid, "home": r.home, "away": r.away,
                        "home_name": r.home_name, "away_name": r.away_name,
                        "kickoff_label": KICKOFF_LABEL})
        return out

    @app.get("/api/matches/{match_id}/players")
    async def players(match_id: str) -> list[dict[str, str]]:
        roster = (await load(match_id)).match.roster
        return [{"player_id": p.player_id, "name": p.name, "team": p.team,
                 "position": p.position} for p in roster.players]

    @app.post("/api/rooms")
    def create_room(request: Request) -> dict[str, str]:
        room_id = f"r-{uuid.uuid4().hex[:6]}"
        base = os.environ.get(PUBLIC_BASE_ENV) or str(request.base_url)
        return {"room_id": room_id, "join_url": f"{base.rstrip('/')}/?room={room_id}"}

    @app.post("/api/signup")
    def signup(body: Signup) -> dict[str, str]:
        member_id = "m-" + hashlib.sha256(body.user_id.encode()).hexdigest()[:8]
        members[member_id] = {"user_id": body.user_id, "email": body.email}  # mock: memory only
        return {"member_id": member_id, "card_url": f"/card/{member_id}"}

    @app.get("/api/card/{member_id}")
    def card(member_id: str) -> dict[str, Any]:
        if member_id not in members:
            raise HTTPException(404, "unknown member")
        uid = members[member_id]["user_id"]
        score = scores.get(uid, {}).get("final_score", 0)
        return {"member_id": member_id, "display_name": names.get(uid, "Fan"),
                "final_score": score, "qr_payload": f"cr7-member:{member_id}",
                "tier": _tier(score)}

    @app.get("/api/score/{user_id}")
    def score(user_id: str) -> dict[str, Any]:
        if user_id not in scores:
            raise HTTPException(404, "no score yet")
        return scores[user_id]

    @app.get("/api/ops/funnel")
    def funnel(match_id: str = DEFAULT_MATCH_ID) -> dict[str, int]:
        return ops_funnel(match_id, APP_SEED)

    # .........................................................................
    # Room supervision
    # .........................................................................

    def on_final(user_id: str, display_name: str, payload: dict[str, Any]) -> None:
        scores[user_id] = payload  # called from the room thread; dict set is atomic
        names[user_id] = display_name

    def drop_room(room: Room) -> None:
        """Forget a finished or dead room and let its sockets drain and close."""
        rid = room.room_id
        if rooms.get(rid) is room:
            rooms.pop(rid, None)
            task = tasks.pop(rid, None)
            if task is not None and not task.done():
                task.cancel()
            for box in conns.pop(rid, {}).values():
                box.close()

    def on_room_done(room: Room, task: asyncio.Task) -> None:
        if not task.cancelled() and task.exception() is not None:
            logger.error("room %s task died: %r", room.room_id, task.exception())
        room.done = True
        drop_room(room)

    def ensure_task(room: Room) -> None:
        task = tasks.get(room.room_id)
        if task is None or task.done():
            task = asyncio.create_task(room.run(sleep))
            tasks[room.room_id] = task
            task.add_done_callback(lambda t, r=room: on_room_done(r, t))

    async def room_for(rid: str, bundle: MatchBundle) -> Room | None:
        """The live room for ``rid``, a fresh one if it is missing or finished, None if full."""
        room = rooms.get(rid)
        if room is not None and room.done:
            drop_room(room)
            room = None
        if room is not None:
            return room
        if len(rooms) >= room_cap:
            return None
        fresh = await asyncio.to_thread(Room, rid, bundle, propose=propose, now=now,
                                        speed=speed, on_final=on_final)
        room = rooms.get(rid)  # another join may have created it while we built ours
        if room is None or room.done:
            if len(rooms) >= room_cap:
                return None
            rooms[rid] = room = fresh
        return room

    # .........................................................................
    # WebSocket
    # .........................................................................

    async def read_join(websocket: WebSocket, query_room: str) -> Join | None:
        """First message: a valid join within JOIN_TIMEOUT_S, else the socket is closed."""
        try:
            first = await asyncio.wait_for(websocket.receive_json(), JOIN_TIMEOUT_S)
            if first.get("type") != "join":
                raise ValueError("first message must be join")
            join = Join.model_validate(first.get("data") or {})
            if join.room_id is None:
                join.room_id = query_room or None
            if join.room_id and not ROOM_ID_RE.match(join.room_id):
                raise ValueError("bad room_id")
            return join
        except asyncio.TimeoutError:
            await _safe_close(websocket, WS_CLOSE_JOIN_TIMEOUT)
        except (ValueError, ValidationError, AttributeError, WebSocketDisconnect, RuntimeError):
            await _safe_close(websocket, WS_CLOSE_BAD_JOIN)
        return None

    @app.websocket("/ws")
    async def ws(websocket: WebSocket, match_id: str = DEFAULT_MATCH_ID,
                 room_id: str = "") -> None:
        await websocket.accept()
        join = await read_join(websocket, room_id)
        if join is None:
            return
        try:
            bundle = await asyncio.to_thread(src.get, join.match_id or match_id)
        except (KeyError, ImportError, FileNotFoundError):
            await _safe_close(websocket, WS_CLOSE_UNKNOWN_MATCH)
            return
        rid = join.room_id or f"r-{uuid.uuid4().hex[:6]}"
        room = await room_for(rid, bundle)
        if room is None:
            await _safe_close(websocket, WS_CLOSE_BUSY)
            return

        loop = asyncio.get_running_loop()
        outbox = Outbox()
        user_id = f"u-{uuid.uuid4().hex[:8]}"

        def send(env: dict[str, Any]) -> None:  # any thread
            try:
                loop.call_soon_threadsafe(outbox.push, env)
            except RuntimeError:
                pass  # loop is shutting down

        conns.setdefault(rid, {})[user_id] = outbox
        names[user_id] = join.display_name
        await asyncio.to_thread(room.add_member, user_id, join.display_name, join.team,
                                join.player_id, send)
        ensure_task(room)

        async def writer() -> None:
            while (env := await outbox.get()) is not None:
                await websocket.send_json(env)

        async def reader() -> None:
            while True:
                try:
                    msg = await websocket.receive_json()
                    kind, data = msg["type"], msg.get("data") or {}
                except (ValueError, KeyError, TypeError, AttributeError):
                    continue  # malformed message: ignore
                if kind == "answer":
                    await asyncio.to_thread(room.answer, user_id, str(data.get("qid")),
                                            str(data.get("option")))
                elif kind == "hint.request":
                    await asyncio.to_thread(room.hint_request, user_id, str(data.get("qid")))
                elif kind == "control":
                    await asyncio.to_thread(room.control, user_id, str(data.get("action")),
                                            data.get("value"))
                elif kind == "feed.resume":
                    await asyncio.to_thread(room.feed_resume, user_id)

        writer_task = asyncio.create_task(writer())
        reader_task = asyncio.create_task(reader())

        async def cleanup() -> None:
            for t in (writer_task, reader_task):
                t.cancel()
            await asyncio.gather(writer_task, reader_task, return_exceptions=True)
            await _safe_close(websocket, WS_CLOSE_TRY_AGAIN if outbox.overflowed
                              else WS_CLOSE_NORMAL)
            conns.get(rid, {}).pop(user_id, None)
            await asyncio.to_thread(room.remove_member, user_id)
            if not room.humans:
                drop_room(room)

        try:
            await asyncio.wait({writer_task, reader_task}, return_when=asyncio.FIRST_COMPLETED)
        except asyncio.CancelledError:
            pass  # server shutdown or the client's task scope ended; clean up below
        finally:
            job = asyncio.ensure_future(cleanup())
            try:
                await asyncio.shield(job)  # cleanup finishes even if this handler is cancelled
            except asyncio.CancelledError:
                pass

    return app


app = create_app()
