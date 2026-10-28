"""One fan's demo run: plays the highlights timeline cue by cue and scores the calls.

``run`` sends every cue at its time (elapsed = sum of requested sleeps, not wall clock);
``call.lock`` becomes ``call.locked`` + ``call.result``. ``answer`` takes one pick per open call.
Forecasts: ``forecast_pick`` / ``forecast_final`` return the reply to send; ``run`` settles due
windows (and the final) just before the ``demo.clock`` / ``demo.half_time`` / ``demo.full_time``
cue that ends them. Calls, forecasts and the player bonus (``bonus.py``: first half added
before ``demo.half_time``, the rest before ``demo.full_time``) share one ``total``. ``demo.half_time`` / ``demo.full_time``
carry the room board, breakdown, badges and stamp (``room.py``); ``signup`` adds +500 once.
"""

from __future__ import annotations


import math
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from backend.demo.forecasts import (EXACT_POINTS, FINAL_OPTIONS, FINAL_POINTS, WINDOWS,
                                    ForecastDef, outcome, result_of, score_final, score_window,
                                    window)
from backend.demo import bonus, room
from backend.demo.names import TEAMS, clock_label
from backend.demo.points import score_call
from backend.demo.script import OPTIONS, Timeline

Send = Callable[[dict], Awaitable[None]]
Sleep = Callable[[float], Awaitable[None]]


_DEFS = {d.key: d for d in WINDOWS}
_FINAL_IDS = {opt for opt, _ in FINAL_OPTIONS}
_NOT_LIVE = {"demo.jump", "demo.half_time", "demo.full_time"}
MAX_GOALS = 9


@dataclass(frozen=True)
class _Pending:
    """An accepted window pick waiting for match time ``t1`` in ``half``."""

    defn: ForecastDef
    pick: str
    half: int
    t0: float
    t1: float


def _env(kind: str, data: dict) -> dict:
    return {"type": kind, "data": data}


def _rejected(key: Any, reason: str) -> dict:
    return _env("forecast.rejected", {"key": key if isinstance(key, str) else None,
                                      "reason": reason})


def _options(pairs: tuple[tuple[str, str], ...]) -> list[dict]:
    return [{"id": opt, "label": label} for opt, label in pairs]


def _goals(n: Any) -> bool:
    return isinstance(n, int) and not isinstance(n, bool) and 0 <= n <= MAX_GOALS


FORECASTS_INFO = {
    "windows": [{"key": d.key, "label": d.label, "minutes": d.minutes, "points": d.points,
                 "question": d.question, "options": _options(d.options)} for d in WINDOWS],
    "final": {"points": FINAL_POINTS, "exact_points": EXACT_POINTS,
              "options": _options(FINAL_OPTIONS)},
}


class DemoSession:
    """State of one demo: running total, streak, the open call and the picks so far."""

    def __init__(self, timeline: Timeline, team: str, code: str | None = None,
                 players: Any = None, stats: bonus.Stats | None = None) -> None:
        self.timeline = timeline
        self.team = team  # display code, ARS or CHE
        self.players = bonus.sanitise(team, players)  # backed player ids, at most 2
        self._stats = stats
        self.bonus_points = 0  # player bonus added to the total so far
        self.code = code or room.room_code()  # this fan's demo room
        self.total = 0
        self.streak = 0
        self.best_streak = 0
        self.call_points = 0
        self.forecast_points = 0  # windows and the final; the signup bonus is in neither
        self.correct_calls = 0
        self.window_hits = 0
        self.final_right = False
        self.saved = False  # signed up (the email itself is never kept)
        self.open_call: str | None = None
        self._picks: dict[str, str] = {}
        self._live: dict | None = None  # latest demo.clock data of the running Play, else None
        self._score: list[int] = [0, 0]  # score of the latest demo.clock
        self._pending: dict[str, _Pending] = {}
        self._final: dict | None = None
        self._final_locked = False

    def answer(self, call_id: Any, pick: Any) -> bool:
        """Record a pick for the open call; True if accepted (first answer, valid pick)."""
        if not isinstance(call_id, str) or not isinstance(pick, str):
            return False
        if call_id != self.open_call or call_id in self._picks or pick not in OPTIONS:
            return False
        self._picks[call_id] = pick
        return True

    def forecast_pick(self, key: Any, pick: Any) -> dict:
        """Pick a window forecast; returns ``forecast.locked`` or ``forecast.rejected``."""
        defn = _DEFS.get(key) if isinstance(key, str) else None
        if defn is None or not isinstance(pick, str) or pick not in dict(defn.options):
            return _rejected(key, "bad_pick")
        if self._live is None:
            return _rejected(key, "not_live")
        if key in self._pending:
            return _rejected(key, "pending")
        half, t0 = self._live["half"], self._live["t"]
        _, t1 = window(defn, half, t0, self.timeline.half_ends.get(half, math.inf))
        self._pending[key] = _Pending(defn, pick, half, t0, t1)
        return _env("forecast.locked", {"key": key, "pick": pick,
                                        "from_clock": clock_label(half, t0),
                                        "to_clock": clock_label(half, t1)})

    def forecast_final(self, result: Any, home: Any, away: Any) -> dict:
        """Set or change the full-time pick; returns ``forecast.final_saved`` or a rejection."""
        if self._final_locked:
            return _rejected("final", "locked")
        if (not isinstance(result, str) or result not in _FINAL_IDS or not _goals(home) or not _goals(away)
                or result != result_of(home, away)
                or home < self._score[0] or away < self._score[1]):
            return _rejected("final", "bad_pick")
        self._final = {"result": result, "home": home, "away": away}
        return _env("forecast.final_saved", dict(self._final))

    def signup(self, email: Any) -> dict:
        """Save progress once: +500. The email is only validated, never kept or echoed."""
        if self.saved:
            return _env("signup.rejected", {"reason": "already"})
        if not room.valid_email(email):
            return _env("signup.rejected", {"reason": "bad_email"})
        self.saved = True
        self.total += room.SIGNUP_BONUS
        return _env("signup.saved", {"bonus": room.SIGNUP_BONUS, "total": self.total})

    def _bonus(self, halves: tuple[int, ...], clean_sheet: bool) -> list[dict]:
        """Bonus card of each backed player over ``halves`` (empty if none backed)."""
        if not self.players:
            return []
        st = self._stats = self._stats or bonus.match_stats()
        return [bonus.player_bonus(pid, halves, clean_sheet, st) for pid in self.players]

    def _add_bonus(self, upto: int) -> None:
        """Bring the bonus in the total up to the players' points so far; each point added once.

        ``upto`` 1: first-half stats (half time); 2: the whole match plus clean sheet (full time).
        """
        cards = self._bonus((1,) if upto == 1 else (1, 2), upto == 2)
        due = sum(c["points"] for c in cards) - self.bonus_points
        self.total += due
        self.bonus_points += due

    def _with_room(self, cue_type: str, data: dict) -> dict:
        """Cue data plus the room / result fields of half time and full time (copy, not shared)."""
        if cue_type == "demo.half_time":
            players = room.board(self.total, self.saved, room.friend_totals(self.timeline), "ht")
            return {**data, "room": {"code": self.code, "players": players},
                    "breakdown": {"calls": self.call_points, "forecasts": self.forecast_points,
                                  "players": self.bonus_points},
                    "players": self._bonus((1,), False),
                    "you": {"rank": room.rank_of(players)}, "total": self.total,
                    "saved": self.saved}
        if cue_type == "demo.full_time":
            players = room.board(self.total, self.saved, room.friend_totals(self.timeline), "ft")
            rank = room.rank_of(players)
            pct = room.top_pct(self.total, room.crowd_totals(self.timeline))
            hits = self.window_hits + self.final_right  # the full-time result is a forecast too
            fc = (f"{hits} forecast{'' if hits == 1 else 's'} hit" if hits else "Forecasts open every match") + (
                " · full-time result nailed" if self.final_right else "")
            cards = self._bonus((1, 2), True)
            pb = ("Back players next match" if not cards else
                  "Earned by the player you backed" if len(cards) == 1
                  else f"Earned by the {len(cards)} players you backed")
            rows = [
                {"key": "rt", "label": "REAL-TIME CALLS", "points": self.call_points,
                 "detail": f"{self.correct_calls} correct calls · best streak of {self.best_streak}"},
                {"key": "fc", "label": "FORECAST", "detail": fc, "points": self.forecast_points},
                {"key": "pb", "label": "PLAYER BONUS", "detail": pb,
                 "points": self.bonus_points}]
            return {**data, "rows": rows,
                    "badges": room.badges(self.final_right, self.best_streak, room.good_eye(cards)),
                    "stamp": room.stamp(rank, self.code, pct),
                    "room": {"code": self.code, "players": players}, "players": cards,
                    "total": self.total, "saved": self.saved}
        return data

    async def _settle(self, send: Send, due: Callable[[_Pending], bool]) -> None:
        """Score and send ``forecast.result`` for every pending window that ``due`` accepts."""
        for key, p in [(k, p) for k, p in self._pending.items() if due(p)]:
            del self._pending[key]
            got = outcome(self.timeline.events, key, p.half, p.t0, p.t1)
            points = score_window(p.defn, p.pick, got)
            self.total += points
            self.forecast_points += points
            self.window_hits += points > 0
            text = ("Forecast hit" if points else
                    "Not this time" if got != "none" else
                    "Even split. No points lost." if key == "f5" else
                    "No shots in that spell. No points lost.")
            await send(_env("forecast.result", {
                "key": key, "pick": p.pick, "outcome": got, "correct": p.pick == got,
                "points": points, "total": self.total, "to_clock": clock_label(p.half, p.t1),
                "text": text}))

    async def _settle_final(self, send: Send) -> None:
        if self._final is None:
            return
        final = self.timeline.final_score
        f = self._final
        result_points, exact_points = score_final(f["result"], f["home"], f["away"], final)
        self.total += result_points + exact_points
        self.forecast_points += result_points + exact_points
        self.final_right = result_points > 0
        await send(_env("forecast.final_result", {
            **f, "final": list(final), "result_points": result_points,
            "exact_points": exact_points, "points": result_points + exact_points,
            "total": self.total}))

    async def _before(self, cue_type: str, data: dict, send: Send) -> None:
        """Forecast messages due before a cue goes out, then the live state that cue sets.

        Jump / half time / full time end the Play first, so a pick that arrives while their
        settlement is being sent is rejected (not_live) instead of left pending forever.
        """
        if cue_type in _NOT_LIVE:
            self._live = None
        if cue_type == "demo.clock":
            await self._settle(send, lambda p: p.half < data["half"] or p.t1 <= data["t"])
            if data["half"] == 2 and not self._final_locked:
                self._final_locked = True
                await send(_env("forecast.final_locked", dict(self._final or {})))
            self._live, self._score = data, list(data["score"])
        elif cue_type == "demo.half_time":
            await self._settle(send, lambda p: p.half == 1)
            self._add_bonus(1)
        elif cue_type == "demo.full_time":
            await self._settle(send, lambda p: True)
            await self._settle_final(send)
            self._add_bonus(2)

    async def run(self, send: Send, sleep: Sleep) -> None:
        """Send ``demo.session`` then every cue at its time, ending with ``demo.full_time``."""
        await send(_env("demo.session", {"teams": {"home": TEAMS["NBR"], "away": TEAMS["CAL"]},
                                         "you": {"team": self.team,
                                                 "players": [bonus.card(p) for p in self.players]},
                                         "duration": self.timeline.duration,
                                         "ai_lines": self.timeline.ai_lines,
                                         "forecasts": FORECASTS_INFO,
                                         "room": {"code": self.code,
                                                  "friends": room.friend_progress(self.timeline)}}))
        elapsed = 0.0
        for cue in self.timeline.cues:
            delay = cue.at - elapsed
            if delay > 0:
                await sleep(delay)
                elapsed += delay
            if cue.type == "call.lock":
                await self._lock(cue.data["call_id"], send)
                continue
            if cue.type == "call.open":
                self.open_call = cue.data["call_id"]
            await self._before(cue.type, cue.data, send)
            await send(_env(cue.type, self._with_room(cue.type, cue.data)))

    async def _lock(self, call_id: str, send: Send) -> None:
        """Close the call, score the pick and send ``call.locked`` + ``call.result``."""
        if self.open_call == call_id:
            self.open_call = None
        answer = self.timeline.calls[call_id].answer
        line = self.timeline.lines.get(call_id)
        pick = self._picks.get(call_id)
        score = score_call(pick, answer, self.streak)
        self.total += score.points
        self.call_points += score.points
        self.streak = score.streak
        self.best_streak = max(self.best_streak, score.streak)
        if score.correct:
            self.correct_calls += 1
        await send(_env("call.locked", {"call_id": call_id}))
        await send(_env("call.result", {
            "call_id": call_id, "answer": answer, "pick": pick, "too_quick": score.too_quick,
            "correct": score.correct, "points": score.points,
            "streak_bonus": score.streak_bonus, "total": self.total, "streak": self.streak,
            "explain": line.explain if line else ""}))
