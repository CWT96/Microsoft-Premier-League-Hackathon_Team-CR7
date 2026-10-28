"""main.py — FastAPI app: /ws replay sessions and the REST endpoints of contracts/README.md.

Last Modified: 2026-10-25 02:10, final review: abuse limits, random ids, reaper
Inputs:  MatchSource (default: ingest + detector + model, loaded lazily); env REPLAY_SPEED,
         CORS_ORIGINS (comma list, default "*"), MAX_ROOMS (200), MAX_ROOM_MEMBERS (12),
         MAX_SOCKETS_PER_IP (6), TRUST_PROXY (default true), PUBLIC_BASE_URL
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
  - limits: frames over 4 KB close the socket (1009), 10 msgs/s (burst 20) per socket (1008),
    sockets per IP and members per room are capped, a join to an unknown room_id is refused,
    idle rooms are reaped (no human for 60 s, a lobby nobody resumes for 15 min)

CAUTION: rooms, scores and sign-ups live in this process's memory, so the app MUST run with a
         single worker. Start it as
         ``uvicorn backend.app.main:app --workers 1 --ws-max-size 4096`` (the size is also
         enforced in code). The per-IP cap keys on the first X-Forwarded-For address while
         TRUST_PROXY is true (the Azure Container Apps ingress sets it), else on the socket
         peer. Set TRUST_PROXY=false when the app is reachable without a proxy, because the
         header is then client-controlled. A restart restarts every match.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import secrets
import threading
import time
import uuid
from collections import OrderedDict, deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Callable, Protocol

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError, field_validator

from backend.agents import llm
from backend.app.replay import APP_SEED, MatchBundle, Room, RoomFull, Sleep, build_bundle
from backend.core.constants import DEFAULT_MATCH_ID
from backend.demo.router import make_demo_router
from backend.scoring.scoring import tier_for
from backend.social.funnel import ops_funnel

logger = logging.getLogger(__name__)

KICKOFF_LABEL = "Tonight, 8:00 pm"
PUBLIC_BASE_ENV = "PUBLIC_BASE_URL"
CORS_ENV = "CORS_ORIGINS"
MAX_ROOMS_ENV = "MAX_ROOMS"
DEFAULT_MAX_ROOMS = 200
TRUST_PROXY_ENV = "TRUST_PROXY"
MAX_SOCKETS_PER_IP_ENV = "MAX_SOCKETS_PER_IP"
DEFAULT_MAX_SOCKETS_PER_IP = 6
MAX_FRAME_BYTES = 4096
RATE_PER_S = 10.0  # sustained messages per second per socket
RATE_BURST = 20.0
SIGNUP_CAP = 10_000  # oldest sign-ups are evicted beyond this
RESERVED_ROOM_CAP = 10_000
REAP_INTERVAL_S = 5.0
ID_FIELD_MAX = 32
JOIN_TIMEOUT_S = 10.0
OUTBOX_MAX = 300  # queued messages per socket before it is considered too slow
STALE_TYPES = frozenset({"match.state", "match.tracking"})  # dropped first when a queue is full
ROOM_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
DISPLAY_NAME_MAX = 24
EMAIL_MAX = 254
EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"
WS_CLOSE_NORMAL = 1000
WS_CLOSE_POLICY = 1008
WS_CLOSE_TOO_BIG = 1009
WS_CLOSE_TRY_AGAIN = 1013
WS_CLOSE_BAD_JOIN = 4400
WS_CLOSE_UNKNOWN_MATCH = 4404
WS_CLOSE_UNKNOWN_ROOM = 4404
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
        from backend.ingest.loader import list_match_ids
        return list_match_ids()

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
    team: str | None = Field(default=None, max_length=ID_FIELD_MAX)
    player_id: str | None = Field(default=None, max_length=ID_FIELD_MAX)

    @field_validator("display_name", mode="before")
    @classmethod
    def _clean_name(cls, v: Any) -> str:
        name = str(v).strip()[:DISPLAY_NAME_MAX]
        return name or "Fan"


class Signup(BaseModel):
    user_id: str = Field(max_length=64)
    email: str = Field(max_length=EMAIL_MAX, pattern=EMAIL_PATTERN)


class TokenBucket:
    """Per-socket rate limit: ``rate`` messages per second with a burst allowance."""

    def __init__(self, rate: float = RATE_PER_S, burst: float = RATE_BURST,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._rate, self._burst, self._clock = rate, burst, clock
        self._tokens = burst
        self._last = clock()

    def allow(self) -> bool:
        now = self._clock()
        self._tokens = min(self._burst, self._tokens + (now - self._last) * self._rate)
        self._last = now
        if self._tokens < 1.0:
            return False
        self._tokens -= 1.0
        return True


class FrameTooLarge(Exception):
    """A client frame exceeded MAX_FRAME_BYTES."""


async def recv_json(ws: WebSocket) -> Any:
    """Next client frame as JSON; refuses frames over MAX_FRAME_BYTES.

    Raises:
        WebSocketDisconnect: the client left.
        FrameTooLarge: the frame was bigger than MAX_FRAME_BYTES.
        ValueError: the frame was not JSON.
    """
    msg = await ws.receive()
    if msg["type"] == "websocket.disconnect":
        raise WebSocketDisconnect(msg.get("code", WS_CLOSE_NORMAL))
    raw = msg.get("text")
    if raw is None:
        raw = msg.get("bytes") or b""
    size = len(raw.encode()) if isinstance(raw, str) else len(raw)
    if size > MAX_FRAME_BYTES:
        raise FrameTooLarge()
    return json.loads(raw)


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


def client_ip(ws: WebSocket) -> str:
    """Key for the per-IP socket cap: first X-Forwarded-For address behind a trusted proxy."""
    if os.environ.get(TRUST_PROXY_ENV, "true").strip().lower() not in ("0", "false", "no"):
        forwarded = ws.headers.get("x-forwarded-for", "").split(",")[0].strip()
        if forwarded:
            return forwarded[:64]
    return ws.client.host if ws.client else "unknown"


def _tier(score: int) -> str:
    return tier_for(score)


async def _safe_close(ws: WebSocket, code: int) -> None:
    try:
        await ws.close(code)
    except RuntimeError:
        pass  # already closed


def create_app(source: MatchSource | None = None, *, sleep: Sleep = asyncio.sleep,
               now: Callable[[], float] = time.monotonic, speed: float | None = None,
               propose: Callable | None = None, max_rooms: int | None = None,
               max_sockets_per_ip: int | None = None,
               rate_limit: tuple[float, float] = (RATE_PER_S, RATE_BURST)) -> FastAPI:
    """Build the app. ``sleep``, ``now``, ``speed``, ``propose`` and ``rate_limit`` (messages
    per second, burst) are injectable for tests."""
    src: MatchSource = source or DefaultSource()
    room_cap = max_rooms if max_rooms is not None else int(
        os.environ.get(MAX_ROOMS_ENV, DEFAULT_MAX_ROOMS))
    ip_cap = max_sockets_per_ip if max_sockets_per_ip is not None else int(
        os.environ.get(MAX_SOCKETS_PER_IP_ENV, DEFAULT_MAX_SOCKETS_PER_IP))
    rooms: dict[str, Room] = {}
    reserved: OrderedDict[str, None] = OrderedDict()  # room ids handed out by POST /api/rooms
    ip_counts: dict[str, int] = {}
    tasks: dict[str, asyncio.Task] = {}
    conns: dict[str, dict[str, Outbox]] = {}
    scores: dict[str, dict[str, Any]] = {}
    names: dict[str, str] = {}
    members: OrderedDict[str, dict[str, str]] = OrderedDict()  # member_id -> user and email
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
            warm["error"] = repr(exc)  # kept for the logs; never sent to clients
        finally:
            warm["ready"] = True

    def reap_once() -> int:
        """Drop rooms with no human for 60 s and lobbies nobody resumed for 15 min."""
        reaped = 0
        for room in list(rooms.values()):
            reason = room.idle_reason()
            if reason is not None:
                logger.info("reaping room %s (%s)", room.room_id, reason)
                drop_room(room)
                reaped += 1
        return reaped

    async def reaper() -> None:
        while True:
            await asyncio.sleep(REAP_INTERVAL_S)
            reap_once()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        task = asyncio.create_task(warm_up())
        reap_task = asyncio.create_task(reaper())
        yield
        task.cancel()
        reap_task.cancel()
        for t in list(tasks.values()):
            t.cancel()

    app = FastAPI(title="CR7 second screen", lifespan=lifespan)
    origins = [o.strip() for o in os.environ.get(CORS_ENV, "*").split(",") if o.strip()]
    app.add_middleware(CORSMiddleware, allow_origins=origins or ["*"], allow_credentials=False,
                       allow_methods=["*"], allow_headers=["*"])
    app.state.rooms = rooms  # exposed for tests and diagnostics
    app.state.reap = lambda: reap_once()
    app.state.reserved = reserved

    async def load(match_id: str) -> MatchBundle:
        try:
            return await asyncio.to_thread(src.get, match_id)
        except KeyError:
            raise HTTPException(404, "unknown match") from None
        except (ImportError, FileNotFoundError):
            logger.exception("match %s could not be loaded", match_id)
            raise HTTPException(503, "match data not available") from None

    # .........................................................................
    # REST
    # .........................................................................

    @app.get("/healthz")
    async def healthz() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/readyz")
    async def readyz() -> JSONResponse:
        return JSONResponse({"ready": warm["ready"]},
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
        room_id = "r-" + secrets.token_urlsafe(8)
        reserved[room_id] = None
        while len(reserved) > RESERVED_ROOM_CAP:
            reserved.popitem(last=False)
        base = os.environ.get(PUBLIC_BASE_ENV) or str(request.base_url)
        return {"room_id": room_id, "join_url": f"{base.rstrip('/')}/?room={room_id}"}

    @app.post("/api/signup")
    def signup(body: Signup) -> dict[str, str]:
        member_id = "m-" + secrets.token_urlsafe(8)
        members[member_id] = {"user_id": body.user_id, "email": body.email}  # mock: memory only
        while len(members) > SIGNUP_CAP:
            members.popitem(last=False)
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
            first = await asyncio.wait_for(recv_json(websocket), JOIN_TIMEOUT_S)
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
        except FrameTooLarge:
            await _safe_close(websocket, WS_CLOSE_TOO_BIG)
        except (ValueError, ValidationError, AttributeError, WebSocketDisconnect, RuntimeError):
            await _safe_close(websocket, WS_CLOSE_BAD_JOIN)
        return None

    @app.websocket("/ws")
    async def ws(websocket: WebSocket, match_id: str = DEFAULT_MATCH_ID,
                 room_id: str = "") -> None:
        await websocket.accept()
        ip = client_ip(websocket)
        if ip_counts.get(ip, 0) >= ip_cap:  # pre-join sockets count too
            await _safe_close(websocket, WS_CLOSE_BUSY)
            return
        ip_counts[ip] = ip_counts.get(ip, 0) + 1
        try:
            await serve(websocket, match_id, room_id)
        finally:
            ip_counts[ip] -= 1
            if ip_counts[ip] <= 0:
                ip_counts.pop(ip, None)

    async def serve(websocket: WebSocket, match_id: str, query_room: str) -> None:
        join = await read_join(websocket, query_room)
        if join is None:
            return
        try:
            bundle = await asyncio.to_thread(src.get, join.match_id or match_id)
        except (KeyError, ImportError, FileNotFoundError):
            await _safe_close(websocket, WS_CLOSE_UNKNOWN_MATCH)
            return
        rid = join.room_id
        if rid is None:  # no room asked for: a private solo room
            rid = "r-" + secrets.token_urlsafe(8)
        elif rid not in rooms and rid not in reserved:
            await _safe_close(websocket, WS_CLOSE_UNKNOWN_ROOM)  # never handed out
            return
        player_id = join.player_id
        if player_id is not None and player_id not in bundle.match.roster.by_id():
            logger.warning("join with unknown player_id ignored")
            player_id = None
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

        try:
            await asyncio.to_thread(room.add_member, user_id, join.display_name, join.team,
                                    player_id, send)
        except RoomFull:
            await _safe_close(websocket, WS_CLOSE_BUSY)
            return
        conns.setdefault(rid, {})[user_id] = outbox
        names[user_id] = join.display_name
        ensure_task(room)
        bucket = TokenBucket(*rate_limit)

        async def writer() -> None:
            while (env := await outbox.get()) is not None:
                await websocket.send_json(env)

        async def reader() -> int | None:
            """Route client messages; returns a close code when the client misbehaves."""
            while True:
                try:
                    msg = await recv_json(websocket)
                    kind, data = msg["type"], msg.get("data") or {}
                except FrameTooLarge:
                    return WS_CLOSE_TOO_BIG
                except (ValueError, KeyError, TypeError, AttributeError):
                    continue  # malformed message: ignore
                if not bucket.allow():
                    return WS_CLOSE_POLICY
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
            code = WS_CLOSE_NORMAL
            if outbox.overflowed:
                code = WS_CLOSE_TRY_AGAIN
            elif reader_task.done() and not reader_task.cancelled() \
                    and reader_task.exception() is None and reader_task.result():
                code = reader_task.result()
            await _safe_close(websocket, code)
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

    app.include_router(make_demo_router(sleep=sleep))
    frontend = Path(__file__).resolve().parents[2] / "frontend"
    # last, so /api/*, /ws and /demo/ws match first. The Premier X (from the design templates)
    # is the app at /; the earlier fan app stays reachable at /classic/.
    if (frontend / "fan-app").is_dir():
        app.mount("/classic", StaticFiles(directory=frontend / "fan-app", html=True), name="fan-app")
    if (frontend / "ops-dashboard").is_dir():  # partner report, shown after the phone flow
        @app.get("/ops", include_in_schema=False)
        def ops_slash() -> RedirectResponse:
            return RedirectResponse("/ops/")  # relative asset paths need the slash

        app.mount("/ops", StaticFiles(directory=frontend / "ops-dashboard", html=True), name="ops-dashboard")
    if (frontend / "premier-x").is_dir():
        app.mount("/", StaticFiles(directory=frontend / "premier-x", html=True), name="premier-x")

    @app.middleware("http")
    async def revalidate_frontend(request, call_next):
        # Browsers must check the app files on every load (a cheap 304 via ETag), so a fresh
        # deploy never runs next to a stale cached pitch.js or screen; fonts and crests may cache.
        response = await call_next(request)
        path = request.url.path
        if not path.startswith(("/api/", "/demo/", "/ws", "/healthz", "/assets/")):
            response.headers.setdefault("Cache-Control", "no-cache")
        return response
    return app


app = create_app()
