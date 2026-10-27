"""One fan's demo run: plays the highlights timeline cue by cue and scores the calls.

``run`` sends every cue at its time (elapsed = sum of requested sleeps, not wall clock);
``call.lock`` becomes ``call.locked`` + ``call.result``. ``answer`` takes one pick per open call.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from backend.demo.names import TEAMS
from backend.demo.points import score_call
from backend.demo.script import OPTIONS, Timeline

Send = Callable[[dict], Awaitable[None]]
Sleep = Callable[[float], Awaitable[None]]


def _env(kind: str, data: dict) -> dict:
    return {"type": kind, "data": data}


class DemoSession:
    """State of one demo: running total, streak, the open call and the picks so far."""

    def __init__(self, timeline: Timeline, team: str) -> None:
        self.timeline = timeline
        self.team = team  # display code, ARS or CHE
        self.total = 0
        self.streak = 0
        self.open_call: str | None = None
        self._picks: dict[str, str] = {}

    def answer(self, call_id: Any, pick: Any) -> bool:
        """Record a pick for the open call; True if accepted (first answer, valid pick)."""
        if not isinstance(call_id, str) or not isinstance(pick, str):
            return False
        if call_id != self.open_call or call_id in self._picks or pick not in OPTIONS:
            return False
        self._picks[call_id] = pick
        return True

    async def run(self, send: Send, sleep: Sleep) -> None:
        """Send ``demo.session`` then every cue at its time, ending with ``demo.full_time``."""
        await send(_env("demo.session", {"teams": TEAMS, "you": {"team": self.team},
                                         "duration": self.timeline.duration}))
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
            await send(_env(cue.type, cue.data))

    async def _lock(self, call_id: str, send: Send) -> None:
        """Close the call, score the pick and send ``call.locked`` + ``call.result``."""
        if self.open_call == call_id:
            self.open_call = None
        answer = self.timeline.calls[call_id].answer
        pick = self._picks.get(call_id)
        score = score_call(pick, answer, self.streak)
        self.total += score.points
        self.streak = score.streak
        await send(_env("call.locked", {"call_id": call_id}))
        await send(_env("call.result", {
            "call_id": call_id, "answer": answer, "pick": pick, "too_quick": score.too_quick,
            "correct": score.correct, "points": score.points,
            "streak_bonus": score.streak_bonus, "total": self.total, "streak": self.streak}))
