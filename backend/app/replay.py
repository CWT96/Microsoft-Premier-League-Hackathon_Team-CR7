"""replay.py — one replay Room: wires detector states, timing, pacers, resolver and scoring.

Last Modified: 2026-10-24 23:55, first version from SPEC sections 4.6, 4.7, 7 and 7a
Inputs:  MatchBundle (match, detector states, model); member actions (answer, hint, control)
Outputs: contract messages pushed to every member's ``send`` callback; ``on_final`` callback

Workflow:
  - ``Room.tick()`` plays one match second (or one real second while paused) and returns the
    real seconds to wait before the next tick; ``Room.run()`` loops it with an injectable sleep
  - per second: events, tracking, timing step, locks, resolver step, catch-up, card delivery
    (one Pacer per member, bots included), per-user ``match.state``
  - pauses: lobby (host resume), half time (resume or 60 s), water break (resume or 30 s)
  - speed: 1x in calm play or while a human has an open card, else the host speed
  - skip "next": runs the seconds up to 3 s before the next scheduled stop (player slot,
    water break, half time, full time) without sending cards
  - full time: results, ``score.final`` and ``leaderboard`` for every human member

CAUTION: code decides, the LLM only phrases (explainer, catch-up, summary). All state is in
memory; the three bot friends are members of every room and have no socket.
"""

from __future__ import annotations

import asyncio
import logging
import math
import functools
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Mapping, Sequence

from backend.agents import catchup, explainer, scoring_summary
from backend.agents import timing_rules as TR
from backend.agents.generator import get_propose
from backend.agents.timing import RulePolicy, TimingAgent
from backend.app.models import FinalScore, Question, QuestionResult, dump
from backend.app.pacer import Pacer
from backend.core.constants import DEFAULT_MATCH_ID
from backend.core.types import (Answer, DetectorState, Match, Moment, Outcome, Roster)
from backend.resolve import Resolver
from backend.scoring import current_streak, final_score
from backend.social.bots import Bot, make_bots
from backend.social.crowd import split_for_result
from backend.social.leaderboard import (GLOBAL_PLAYERS, leaderboard, simulated_global_entries)

logger = logging.getLogger(__name__)

# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
#
# Constants
#
# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

APP_SEED = 42
SPEED_ENV = "REPLAY_SPEED"
DEFAULT_SPEED = 15.0
MIN_SPEED = 1.0
MAX_SPEED = 15.0
CALM_COST = TR.CANDIDATE_COST  # cost at or below this: play at 1x
PAUSE_TICK_S = 1.0  # real seconds between repeated states while paused
HALF_TIME_TIMEOUT_S = 60.0
WATER_BREAK_TIMEOUT_S = 30.0
SKIP_LEAD_STATES = 3  # land this many seconds before the next stop
TYPICAL_CARDS_FALLBACK = 40
TIMED_KINDS = frozenset({"instant", "player_instant"})
BOT_PLAYER_STRIDE = 3  # bot i follows roster player (i * stride + 1)
TIMING_POLICY_ENV = "TIMING_POLICY"
MAX_ROOM_MEMBERS_ENV = "MAX_ROOM_MEMBERS"
DEFAULT_MAX_ROOM_MEMBERS = 12
EMPTY_ROOM_TTL_S = 60.0  # a room without a human is reaped after this
LOBBY_TTL_S = 900.0  # a lobby nobody resumes is reaped after 15 minutes
MAX_CONSECUTIVE_FAILURES = 5  # failing ticks in a row before the room is ended

Send = Callable[[dict[str, Any]], None]
Sleep = Callable[[float], Awaitable[None]]


def _noop_send(_: dict[str, Any]) -> None:
    return None


def default_speed() -> float:
    """Host speed from ``REPLAY_SPEED`` (clamped to 1-15), default 15."""
    try:
        value = float(os.environ.get(SPEED_ENV, DEFAULT_SPEED))
    except ValueError:
        value = DEFAULT_SPEED
    return min(MAX_SPEED, max(MIN_SPEED, value))


def typical_cards_for(match_id: str) -> int:
    """Cards a normal-pace user answers (timing simulator), or a named constant."""
    try:
        from backend.agents.timing_sim import typical_cards
        return int(typical_cards(match_id))
    except (ImportError, AttributeError):
        logger.warning("timing_sim.typical_cards unavailable; using %d", TYPICAL_CARDS_FALLBACK)
        return TYPICAL_CARDS_FALLBACK


class DefaultModel:
    """Thin adapter over ``backend.model`` (imported lazily; fitted once per match)."""

    def __init__(self, match_id: str) -> None:
        self.match_id = match_id

    def option_probs(self, template_id: str, state: DetectorState,
                     params: dict[str, Any]) -> dict[str, float]:
        from backend.model import get_model, option_probs
        return option_probs(template_id, state, params, model=get_model(self.match_id))

    def hint(self, probs_shown: Mapping[str, float]) -> tuple[str, float]:
        from backend.model import hint
        return hint(probs_shown)


@dataclass
class MatchBundle:
    """Everything a room needs about one match; shared read-only between rooms."""

    match: Match
    states: Sequence[DetectorState]  # lobby, every second of both halves, full_time
    model: Any
    n_typical: int = TYPICAL_CARDS_FALLBACK


@dataclass
class Card:
    """One question shown to one member."""

    moment: Moment
    question: Question
    delivered_t: float
    delivered_real: float
    option: str | None = None
    latency_s: float | None = None
    used_hint: bool = False
    hint: tuple[str, float] | None = None
    locked: bool = False
    seq: int = 0  # delivery order for this member
    free_t: float | None = None  # bots: match time when the bot is free again


@dataclass
class Member:
    user_id: str
    display_name: str
    team: str | None
    player_id: str | None
    send: Send
    is_host: bool = False
    bot: Bot | None = None
    open: Card | None = None
    cards: dict[str, Card] = field(default_factory=dict)
    terms: list[str] = field(default_factory=list)
    nudged: bool = False
    next_seq: int = 0

    def __post_init__(self) -> None:
        self.pacer = Pacer(self.user_id)

    def emit(self, type_: str, data: dict[str, Any]) -> None:
        self.send({"type": type_, "data": data})


class RoomFull(Exception):
    """The room already has the maximum number of human members."""


@dataclass
class Pause:
    kind: str  # "lobby" | "half_time" | "water_break"
    started: float
    timeout_s: float | None


# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
#
# Room
#
# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%


def _synchronized(method: Callable) -> Callable:
    """Run a public Room method under the room lock (ticks run in a worker thread)."""
    @functools.wraps(method)
    def wrapper(self: "Room", *args: Any, **kwargs: Any) -> Any:
        with self.lock:
            return method(self, *args, **kwargs)
    return wrapper


class Room:
    """One replay instance with its members, bots, timing agent and resolver.

    Thread model: ``tick`` runs in a worker thread (``run``), the actions (answer, hint,
    control, join, leave) may come from the event loop; all of them take ``self.lock``.
    ``send`` callbacks are called from either thread and must be thread-safe.
    """

    def __init__(self, room_id: str, bundle: MatchBundle, *, propose: Callable | None = None,
                 now: Callable[[], float] = time.monotonic, speed: float | None = None,
                 on_final: Callable[[str, str, dict[str, Any]], None] | None = None,
                 max_members: int | None = None) -> None:
        self.room_id = room_id
        self.lock = threading.RLock()
        self.bundle = bundle
        self.match = bundle.match
        self.match_id = bundle.match.match_id
        self.roster: Roster = bundle.match.roster
        self.states = bundle.states
        self.model = bundle.model
        self.now = now
        self.max_members = max_members if max_members is not None else int(
            os.environ.get(MAX_ROOM_MEMBERS_ENV, DEFAULT_MAX_ROOM_MEMBERS))
        self.empty_since: float | None = now()  # set while no human is present
        self.speed = speed if speed is not None else default_speed()
        self.on_final = on_final
        self._propose = propose or get_propose()
        self.timing = TimingAgent(self._make_policy(), self.match_id, self.roster)
        self.resolver = Resolver(self.roster)
        self.members: dict[str, Member] = {}
        self.names: dict[str, str] = {p.player_id: p.name for p in self.roster.players}
        self.names[self.roster.home] = self.roster.home_name
        self.names[self.roster.away] = self.roster.away_name
        # moments and questions
        self.open_moments: list[Moment] = []
        self.locked_moment_ids: set[str] = set()
        self.deadlines: dict[str, float] = {}  # real-time lock of scheduled moments
        self.questions: dict[str, Question] = {}
        self.moment_qids: dict[str, list[str]] = {}
        self.probs: dict[str, dict[str, float]] = {}
        self.moment_cache: dict[tuple[str, str | None], Question | None] = {}
        self.watched: set[str] = set()
        self.outcomes: dict[str, Outcome] = {}
        self.human_moments: set[str] = set()  # moments some human picked up (bots follow)
        # clock
        self._idx = 0
        self.state: DetectorState = bundle.states[0]
        self._prev: DetectorState | None = None
        self.pause: Pause | None = None
        self._lobby_done = False
        self._skip_pending = False
        self.cost = self.timing.last_cost
        self.done = False
        self._attack_events: list = []
        self._in_attack = False
        self._catchups = 0
        self._add_bots()

    # .........................................................................
    # members
    # .........................................................................

    @staticmethod
    def _make_policy() -> Any:
        if os.environ.get(TIMING_POLICY_ENV) == "llm":
            from backend.agents import llm
            from backend.agents.timing import LLMPolicy
            return LLMPolicy(llm.complete_json)
        return RulePolicy()

    def _add_bots(self) -> None:
        players = self.roster.players
        for i, bot in enumerate(make_bots(APP_SEED)):
            pid = players[(i * BOT_PLAYER_STRIDE + 1) % len(players)].player_id if players else None
            self.members[f"bot-{i}"] = Member(f"bot-{i}", bot.name, None, pid, _noop_send, bot=bot)

    @property
    def humans(self) -> list[Member]:
        return [m for m in self.members.values() if m.bot is None]

    @property
    def bots(self) -> list[Member]:
        return [m for m in self.members.values() if m.bot is not None]

    @_synchronized
    def add_member(self, user_id: str, display_name: str, team: str | None,
                   player_id: str | None, send: Send) -> Member:
        """Add a human member (the first one is the host) and send them ``session``."""
        if len(self.humans) >= self.max_members:
            raise RoomFull(self.room_id)
        member = Member(user_id, display_name, team, player_id, send,
                        is_host=not self.humans)
        self.members[user_id] = member
        self.empty_since = None
        self._send_session(member)
        return member

    def _send_session(self, member: Member) -> None:
        member.emit("session", {"user_id": member.user_id, "match_id": self.match_id,
                                "room_id": self.room_id, "is_host": member.is_host,
                                "friends": [b.display_name for b in self.bots]})

    @_synchronized
    def remove_member(self, user_id: str) -> None:
        """Drop a member; if the host left, the first remaining human becomes host."""
        left = self.members.pop(user_id, None)
        if left is not None and left.is_host and self.humans:
            heir = self.humans[0]
            heir.is_host = True
            self._send_session(heir)  # fresh session carries is_host true
        if not self.humans:
            self.empty_since = self.now()

    def idle_reason(self) -> str | None:
        """Why the room should be reaped ("empty" or "lobby"), or None while it is in use."""
        now = self.now()
        if not self.humans and self.empty_since is not None \
                and now - self.empty_since >= EMPTY_ROOM_TTL_S:
            return "empty"
        if self.pause is not None and self.pause.kind == "lobby" \
                and now - self.pause.started >= LOBBY_TTL_S:
            return "lobby"
        return None

    def _broadcast(self, type_: str, data: dict[str, Any]) -> None:
        for m in self.humans:
            m.emit(type_, data)

    # .........................................................................
    # actions from members
    # .........................................................................

    @_synchronized
    def answer(self, user_id: str, qid: str, option: str) -> bool:
        """Record an answer; False if rejected (no card, locked, late, bad option, repeat)."""
        m = self.members.get(user_id)
        card = m.open if m else None
        if card is None or card.question.qid != qid or card.locked or card.option is not None:
            return False
        if card.moment.kind in TIMED_KINDS and self.state.t >= card.question.lock_at:
            return False
        if card.moment.kind not in TIMED_KINDS and \
                self.now() >= self.deadlines.get(card.moment.moment_id, math.inf):
            return False  # stack and break cards close in real time
        if option not in {o.id for o in card.question.options}:
            return False
        card.option = option
        card.latency_s = max(0.0, self.now() - card.delivered_real)
        m.pacer.on_answer(card.moment.moment_id, self.state.t)
        m.open = None
        self._pump_member(m, self.state)
        return True

    @_synchronized
    def hint_request(self, user_id: str, qid: str) -> bool:
        """Reveal the AI pick for the open card; marks the card as hinted."""
        m = self.members.get(user_id)
        card = m.open if m else None
        if card is None or card.question.qid != qid or card.locked \
                or not card.question.hint_available:
            return False
        if card.hint is not None:
            return False  # one hint per card; repeats are ignored
        card.hint = self.model.hint(self.probs[qid])
        card.used_hint = True
        m.emit("hint", {"qid": qid, "ai_pick": card.hint[0], "p": card.hint[1]})
        return True

    @_synchronized
    def feed_resume(self, user_id: str) -> None:
        """The user tapped the nudge."""
        m = self.members.get(user_id)
        if m is not None:
            m.pacer.resume()
            m.nudged = False
            self._pump_member(m, self.state)

    @_synchronized
    def control(self, user_id: str, action: str, value: Any = None) -> bool:
        """Host-only demo controls: speed, skip ("next"), resume."""
        m = self.members.get(user_id)
        if m is None or not m.is_host or self.done:
            return False
        if action == "speed":
            if isinstance(value, bool) or not isinstance(value, (int, float)) \
                    or not MIN_SPEED <= value <= MAX_SPEED:
                return False
            self.speed = float(value)
            return True
        if action == "resume" or (action == "skip" and self.pause is not None):
            if self.pause is None:
                return False
            self._end_pause()
            return True
        if action == "skip" and value == "next":
            self._skip_pending = True
            return True
        return False

    # .........................................................................
    # clock
    # .........................................................................

    @_synchronized
    def tick(self) -> float | None:
        """Advance the replay by one step. Returns real seconds to wait, None when finished."""
        if self.done:
            return None
        if self._skip_pending:
            self._skip_pending = False
            self._skip_ahead()
        if self.pause is not None:
            return self._pause_tick()
        if self._idx >= len(self.states):
            self._finish()
            return None
        state = self.states[self._idx]
        self._idx += 1
        self._play(state)
        return None if self.done else self._delay()

    async def run(self, sleep: Sleep = asyncio.sleep) -> None:
        """Play the whole match with ticks in a worker thread; ``sleep`` is injectable.

        A failing step is logged and skipped. After MAX_CONSECUTIVE_FAILURES in a row the
        room ends (``done``), so the task never dies silently or spins forever.
        """
        failures = 0
        while True:
            try:
                delay = await asyncio.to_thread(self.tick)
                failures = 0
            except Exception:  # noqa: BLE001 - one bad second must not kill the room
                failures += 1
                logger.exception("room %s: tick failed (%d in a row)", self.room_id, failures)
                if failures >= MAX_CONSECUTIVE_FAILURES:
                    logger.error("room %s: ending after %d failed ticks in a row",
                                 self.room_id, failures)
                    self.done = True
                    return
                delay = 0.0
            if delay is None:
                return
            await sleep(delay)

    def _delay(self) -> float:
        if self.cost <= CALM_COST or any(m.open for m in self.humans):
            return 1.0
        return 1.0 / self.speed

    def _effective_speed(self) -> float:
        return 1.0 if self._delay() >= 1.0 else self.speed

    def _play(self, state: DetectorState) -> None:
        prev, self._prev = self._prev, state
        self._process(state, loud=True)
        if state.mode == "break" and (prev is None or prev.mode != "break"
                                      or prev.break_kind != state.break_kind):
            if state.break_kind == "half_time":
                self._handle_outcomes(self.resolver.on_half_end(1))
                self._start_pause("half_time", HALF_TIME_TIMEOUT_S)
            else:
                self._start_pause("water_break", WATER_BREAK_TIMEOUT_S)
        elif state.mode == "lobby" and not self._lobby_done:
            self._lobby_done = True
            self._start_pause("lobby", None)
        if state.mode == "full_time" or self._idx >= len(self.states):
            self._finish()

    def _process(self, state: DetectorState, loud: bool) -> None:
        """One match second. ``loud=False`` (skipping) sends nothing but locks and results."""
        self.state = state
        if loud:  # skipped seconds only feed the resolver; the pitch has nothing to animate
            for e in state.new_events:
                self._broadcast("match.event", e.to_contract())
        if loud and self.match.tracking is not None:
            frame = self.match.tracking.at(state.half, state.t)
            if frame is not None:
                self._broadcast("match.tracking", frame.to_contract())
        out = self.timing.step(state)
        for moment in out.moments:
            self._open_moment(moment)
        if loud:
            for d in out.decisions:
                self._broadcast("timing.decision", d.to_contract())
        self.cost = self.timing.last_cost
        self._lock_due(state)
        self._handle_outcomes(self.resolver.step(state.half, state.t, state.new_events))
        self._track_attack(state, loud)
        self._tick_bots(state)
        if loud:
            self._pump(state)
            self._emit_states(state)
            self._emit_nudges()

    # .........................................................................
    # pauses and skip
    # .........................................................................

    def _start_pause(self, kind: str, timeout_s: float | None) -> None:
        """Freeze the replay; open instant cards lock so the break card can reach everyone."""
        self.pause = Pause(kind, self.now(), timeout_s)
        for m in [m for m in self.open_moments if m.kind in TIMED_KINDS]:
            self._lock_moment(m, self.state.t, self.state.half, early=True)
        self._pump(self.state)

    def _pause_tick(self) -> float:
        now = self.now()
        self._lock_scheduled_due(now)
        if self.pause.timeout_s is not None and now - self.pause.started >= self.pause.timeout_s:
            self._end_pause()
            return 0.0
        self._tick_bots(self.state)
        self._pump(self.state)
        self._emit_states(self.state)
        self._emit_nudges()
        return PAUSE_TICK_S

    def _end_pause(self) -> None:
        """Leave a pause. Break stacks lock now; the kickoff stack stays open for its window."""
        kind, self.pause = self.pause.kind, None
        for m in [m for m in self.open_moments if m.kind not in TIMED_KINDS]:
            if m.kind == "kickoff_stack" and kind == "lobby":
                self.deadlines[m.moment_id] = self.now() + (m.lock_at - m.open_at)
            else:
                self._lock_moment(m, self.state.t, self.state.half, early=True)

    def _skip_ahead(self) -> None:
        """Play silently up to SKIP_LEAD_STATES before the next scheduled stop (never past it)."""
        ahead = self.states[self._idx:]
        stop = self.timing.peek_next_stop(ahead, scheduled_only=True)
        if stop is not None:
            at = (stop.half, stop.t)
            stop_idx = next((i for i, s in enumerate(ahead) if (s.half, s.t) == at),
                            len(ahead) - 1)
        else:
            stop_idx = next((i for i, s in enumerate(ahead) if s.mode == "full_time"),
                            len(ahead) - 1)
        target = self._idx + max(0, stop_idx - SKIP_LEAD_STATES)
        if target <= self._idx:
            return
        while self._idx < target:
            state = self.states[self._idx]
            self._idx += 1
            self._prev = state
            self._process(state, loud=False)
        self._emit_states(self.state)

    # .........................................................................
    # moments, questions, cards
    # .........................................................................

    def _open_moment(self, moment: Moment) -> None:
        self.open_moments.append(moment)
        if moment.kind == "kickoff_stack":
            self.deadlines[moment.moment_id] = math.inf  # starts at the host's resume
        elif moment.kind not in TIMED_KINDS:
            self.deadlines[moment.moment_id] = self.now() + (moment.lock_at - moment.open_at)

    def _question_for(self, member: Member, moment: Moment, state: DetectorState
                      ) -> Question | None:
        key = (moment.moment_id, member.player_id if moment.category == "player" else None)
        if key not in self.moment_cache:
            self.moment_cache[key] = self._propose(state, moment, member.player_id,
                                                   roster=self.roster)
            q = self.moment_cache[key]
            if q is not None:
                self.questions[q.qid] = q
                self.moment_qids.setdefault(moment.moment_id, []).append(q.qid)
                self.probs[q.qid] = self._shown_probs(q, state)
        return self.moment_cache[key]

    def _shown_probs(self, q: Question, state: DetectorState) -> dict[str, float]:
        params = dict(q.params)
        if q.player_id:
            params["player_id"] = q.player_id
        try:
            raw = self.model.option_probs(q.template_id, state, params)
        except (KeyError, ValueError) as exc:
            logger.warning("model has no probabilities for %s (%r); using 50/50", q.qid, exc)
            raw = {}
        shown = {o.id: raw.get(o.id, 0.0) for o in q.options}
        total = sum(shown.values())
        if total <= 0:
            return {k: 1.0 / len(shown) for k in shown}
        return {k: v / total for k, v in shown.items()}

    def _pump(self, state: DetectorState) -> None:
        for m in self.humans:
            self._pump_member(m, state)
        self._pump_bots(state)

    def _pump_bots(self, state: DetectorState) -> None:
        for m in self.bots:
            self._pump_member(m, state)

    def _pump_member(self, m: Member, state: DetectorState) -> None:
        if state.attack_active or state.mode in ("attack", "full_time"):
            return
        while m.open is None:
            moments = self.open_moments if m.bot is None else [
                x for x in self.open_moments if x.moment_id in self.human_moments]
            moment = m.pacer.next_card(state.t, state.half, moments, self.locked_moment_ids)
            if moment is None:
                return
            q = self._question_for(m, moment, state)
            if q is None:
                if moment.category == "player":
                    m.pacer.lock_now([moment.moment_id])  # only this user lacks a question
                else:
                    self._drop_moment(moment)
                continue
            card = Card(moment, q, state.t, self.now(), seq=m.next_seq)
            m.next_seq += 1
            m.open = m.cards[q.qid] = card
            if m.bot is None:
                self.human_moments.add(moment.moment_id)
                m.emit("question.open", dump(q))
                self._pump_bots(state)  # bots follow the moment a human just picked up
            else:
                self._bot_decide(m, card, state)

    def _drop_moment(self, moment: Moment) -> None:
        """Nobody can use this moment (generator returned None)."""
        self.locked_moment_ids.add(moment.moment_id)
        if moment in self.open_moments:
            self.open_moments.remove(moment)
        for other in self.members.values():
            other.pacer.lock_now([moment.moment_id])

    def _bot_decide(self, m: Member, card: Card, state: DetectorState) -> None:
        q = card.question
        option, latency = m.bot.decide(q, self.probs[q.qid])
        window = q.lock_at - state.t if card.moment.kind in TIMED_KINDS else math.inf
        if option is not None and latency <= window:
            card.option, card.latency_s = option, latency
            card.free_t = state.t + latency if card.moment.kind in TIMED_KINDS else state.t
            if card.free_t <= state.t:
                m.pacer.on_answer(card.moment.moment_id, state.t)
                m.open = None

    def _tick_bots(self, state: DetectorState) -> None:
        for m in self.bots:
            card = m.open
            if card is not None and card.option is not None and card.free_t is not None \
                    and card.free_t <= state.t:
                m.pacer.on_answer(card.moment.moment_id, state.t)
                m.open = None
            if m.pacer.paused:
                m.pacer.resume()  # bots never wander off

    # .........................................................................
    # locks
    # .........................................................................

    def _lock_due(self, state: DetectorState) -> None:
        self._lock_scheduled_due(self.now())
        attack = state.attack_active or state.mode == "attack"
        for m in [m for m in self.open_moments if m.kind in TIMED_KINDS]:
            if attack or (m.lock_rule == "on_restart" and state.restart_taken):
                self._lock_moment(m, state.t, state.half, early=True)
            elif m.half != state.half or state.t >= m.lock_at:
                self._lock_moment(m, m.lock_at, m.half, early=False)

    def _lock_scheduled_due(self, now: float) -> None:
        for m in [m for m in self.open_moments if m.kind not in TIMED_KINDS]:
            if now >= self.deadlines.get(m.moment_id, math.inf):
                self._lock_moment(m, self.state.t, self.state.half, early=True)

    def _lock_moment(self, moment: Moment, lock_t: float, lock_half: int, early: bool) -> None:
        """Lock a moment for everybody: hide open cards, hand the questions to the resolver."""
        if moment in self.open_moments:
            self.open_moments.remove(moment)
        mid = moment.moment_id
        self.locked_moment_ids.add(mid)
        if moment.kind == "kickoff_stack":  # answered before kickoff, whenever the card locks
            lock_t, lock_half = moment.open_at, moment.half
        for qid in self.moment_qids.get(mid, []):
            holders = [m for m in self.members.values() if qid in m.cards]
            if not holders:
                continue
            for m in holders:
                card = m.cards[qid]
                card.locked = True
                if m.bot is None:
                    m.emit("question.locked", {"qid": qid})
                if m.open is card:
                    if card.option is not None:
                        m.pacer.on_answer(mid, self.state.t)
                    elif early or moment.kind not in TIMED_KINDS:
                        m.pacer.lock_now([mid])
                    else:
                        m.pacer.on_lock(mid, answered=False)
                    m.open = None
            self._watch(self.questions[qid], lock_t, lock_half)

    def _watch(self, q: Question, lock_t: float, lock_half: int) -> None:
        if q.qid in self.watched:
            return
        self.watched.add(q.qid)
        try:
            self.resolver.watch(q, lock_t, lock_half)
        except ValueError as exc:
            logger.warning("resolver cannot watch %s (%r); settling as void", q.qid, exc)
            self._handle_outcomes([Outcome(q.qid, "void", None, (), lock_t, {})])

    # .........................................................................
    # results
    # .........................................................................

    def _handle_outcomes(self, outcomes: Sequence[Outcome]) -> None:
        for o in outcomes:
            q = self.questions.get(o.qid)
            if q is None:
                continue
            self.outcomes[o.qid] = o
            explanation = explainer.explain(o, q, self.names, self.match_id)
            self._send_results(q, o, explanation)

    def _send_results(self, q: Question, o: Outcome, exp: Mapping[str, Any]) -> None:
        ids = [opt.id for opt in q.options]
        holders = [m for m in self.members.values() if q.qid in m.cards]
        room = {m.user_id: m.cards[q.qid].option for m in holders
                if m.bot is None and m.cards[q.qid].option}
        friends = {m.user_id: m.cards[q.qid].option for m in holders
                   if m.bot is not None and m.cards[q.qid].option}
        split = split_for_result(q.qid, self.probs[q.qid], APP_SEED, ids, room, friends)
        for m in holders:
            if m.bot is not None:
                continue
            card = m.cards[q.qid]
            answered = card.option is not None
            correct = None
            if answered and o.status == "resolved":
                correct = card.option == o.correct_option
            term = exp.get("new_term")
            if answered and term and term not in m.terms:
                m.terms.append(term)
            result = QuestionResult.model_validate({
                "qid": q.qid, "status": o.status, "correct_option": o.correct_option,
                "message": exp["message"], "explanation": exp.get("explanation"),
                "new_term": term, "evidence": list(o.evidence), "facts": dict(o.facts),
                "crowd_split": split,
                "you": {"answered": answered, "option": card.option, "correct": correct,
                        "used_hint": card.used_hint, "streak": self._streak(m)}})
            m.emit("question.result", dump(result))

    def _streak(self, m: Member) -> int:
        """Current streak over the cards resolved so far, in issue order (scoring's rule)."""
        return current_streak(self._answers(m), self.outcomes)

    # .........................................................................
    # catch-up, state, nudge
    # .........................................................................

    def _track_attack(self, state: DetectorState, loud: bool) -> None:
        if state.attack_active:
            self._in_attack = True
            self._attack_events.extend(state.new_events)
        elif self._in_attack:
            end = state.last_attack_end_t
            self._attack_events.extend(e for e in state.new_events if end is None or e.t <= end)
            events, self._attack_events, self._in_attack = self._attack_events, [], False
            if loud and events:
                self._catchups += 1
                card = catchup.build_catchup(events, self.roster, f"c-{self._catchups}",
                                             self.match_id)
                card.pop("source", None)
                self._broadcast("catchup.card", card)

    def _emit_states(self, state: DetectorState) -> None:
        speed = self._effective_speed()
        for m in self.humans:
            mode = state.mode
            if m.open is not None and m.open.moment.kind in TIMED_KINDS \
                    and not m.open.locked and mode == "live":
                mode = "game"
            m.emit("match.state", {
                "t": state.t, "half": state.half, "home": self.roster.home,
                "away": self.roster.away,
                "score": {"home": state.score_home, "away": state.score_away},
                "mode": mode, "break_kind": state.break_kind if state.mode == "break" else None,
                "speed": speed, "dull_score": _num(state.dull_score), "cost": _num(self.cost),
                "movement": _num(state.movement), "pressure_m": _num(state.pressure_m),
                "situation": state.situation})

    def _emit_nudges(self) -> None:
        for m in self.humans:
            text = m.pacer.pending_nudge()
            if text and not m.nudged:
                m.nudged = True
                m.emit("nudge", {"text": text})

    # .........................................................................
    # full time
    # .........................................................................

    def _finish(self) -> None:
        if self.done:
            return
        self.done = True
        self.pause = None
        for m in list(self.open_moments):
            self._lock_moment(m, self.state.t, self.state.half, early=True)
        self._handle_outcomes(self.resolver.on_full_end())
        self._send_final_scores()

    def _answers(self, m: Member) -> list[Answer]:
        return [Answer(
            qid=c.question.qid, category=c.question.category, situation=c.moment.situation,
            open_at=c.question.open_at, lock_at=c.question.lock_at, half=c.moment.half,
            option=c.option, answer_latency_s=c.latency_s, used_hint=c.used_hint, seq=c.seq)
            for c in m.cards.values()]

    def _score(self, m: Member, **ranks: Sequence[int]) -> FinalScore:
        return final_score(m.user_id, self.match_id, self._answers(m), self.outcomes,
                           self.bundle.n_typical, m.terms, **ranks)

    def _send_final_scores(self) -> None:
        bot_scores = {b.user_id: self._score(b).final_score for b in self.bots}
        entries = simulated_global_entries(GLOBAL_PLAYERS, APP_SEED)
        global_final = [s for _, s in entries]
        friends_final = list(bot_scores.values())
        raw = {h.user_id: self._score(h).final_score for h in self.humans}
        for h in self.humans:
            room_final = [s for uid, s in raw.items() if uid != h.user_id]
            fs = self._score(h, others_final=global_final, room_final=room_final,
                             friends_final=friends_final)
            beaten = sum(1 for s in friends_final if s < fs.final_score)
            text, _ = scoring_summary.summarize(fs, {"friends_beaten": beaten})
            fs = fs.model_copy(update={"summary": text})
            payload = dump(fs)
            h.emit("score.final", payload)
            if self.on_final is not None:
                self.on_final(h.user_id, h.display_name, payload)
            you = (h.display_name, fs.final_score, True)
            room_entries = [you] + [(o.display_name, raw[o.user_id], False)
                                    for o in self.humans if o is not h]
            friend_entries = [you] + [(b.display_name, bot_scores[b.user_id], False)
                                      for b in self.bots]
            h.emit("leaderboard", leaderboard("room", room_entries))
            h.emit("leaderboard", leaderboard("friends", friend_entries))
            h.emit("leaderboard", leaderboard("global", [you] + [(n, s, False)
                                                               for n, s in entries]))


def _num(x: float | None) -> float | None:
    """JSON-safe number: NaN becomes None."""
    return None if x is None or (isinstance(x, float) and math.isnan(x)) else x


def build_bundle(match_id: str = DEFAULT_MATCH_ID) -> MatchBundle:
    """Load a match, run the detector over it and attach model and typical card count.

    Raises:
        ImportError: if ingest, detect or model are not importable.
        FileNotFoundError: if the match does not exist.
    """
    from backend.detect.detector import Detector
    from backend.ingest import load_match
    match = load_match(match_id)
    states = list(Detector(match.roster, match.roster.home, match.roster.away).run(match))
    return MatchBundle(match, states, DefaultModel(match_id), typical_cards_for(match_id))
