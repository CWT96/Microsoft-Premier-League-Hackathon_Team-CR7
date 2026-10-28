"""watcher.py — stage [4]: settle published questions with the bank's rules as events arrive.

Last Modified: 2026-10-26 16:30, first version: replaces the catalog resolver with bank/settle
Inputs:  the question's bank Candidate (from ``BankGenerator``), events per second (``step``)
Outputs: ``Outcome`` per qid, returned once when it settles

Workflow:
  - ``watch`` looks up the Candidate behind a qid. With ``from_open`` (live cards), a
    lock-counted window is moved back to the card's opening, keeping its length: an event while
    the card is up decides the question (the app then skips users who had not answered yet).
    Stack and water-break cards keep the bank's window (stacks count from the restart).
  - ``step`` appends the second's events and runs ``bank.settle.settle_candidate`` for every
    open question; after full_end every question is closed.

CAUTION: pure code, no LLM. Match clock order is (half, t); the second half restarts at 2700.
"""

from __future__ import annotations

import bisect
from dataclasses import replace
from typing import Sequence

from backend.app.models import Question
from backend.bank.settle import settle_candidate
from backend.bank.types import Candidate
from backend.core.types import MatchEvent, Outcome


class Resolver:
    """Settles bank questions from the event stream."""

    def __init__(self, generator) -> None:
        self._gen = generator
        self._events: list[dict] = []
        self._keys: list[tuple[int, float]] = []
        self._open: dict[str, Candidate] = {}
        self._ended = False
        self._seen: set[str] = set()  # every event id delivered

    def watch(self, question: Question, lock_t: float, lock_half: int | None = None,
              from_open: bool = False) -> None:
        """Start settling ``question``.

        ``lock_t`` / ``lock_half`` are accepted for the app's call shape and not used.
        ``from_open``: count from ``question.open_at`` instead of the bank's lock.

        Raises:
            ValueError: the qid did not come from the bank generator.
        """
        cand = self._gen.candidates.get(question.qid)
        if cand is None:
            raise ValueError(f"no bank candidate behind {question.qid}")
        template = self._gen.bank[cand.template_id]
        w = cand.window
        shift = w.start_t - question.open_at
        if from_open and template.counts_from == "lock" and shift > 0:
            end = None if w.end_t is None else w.end_t - shift  # same length as the text says
            cand = replace(cand, window=replace(w, start_t=question.open_at, end_t=end))
        self._open[question.qid] = cand

    def _since(self, cand: Candidate) -> list[dict]:
        if self._gen.bank[cand.template_id].resolve["primitive"] == "final_state":
            return self._events  # the score at the window end needs every goal
        w = cand.window
        start_half = w.half or w.start_half or 1
        return self._events[bisect.bisect_right(self._keys, (start_half, w.start_t)):]

    def step(self, half: int, t: float, new_events: Sequence[MatchEvent]) -> list[Outcome]:
        """Add one second's events; return the questions that settled."""
        from backend.agents.question_generator import event_dict
        for e in new_events:
            self._events.append(event_dict(e))
            self._keys.append((e.half, e.t))
            self._seen.add(e.id)
            self._ended = self._ended or e.type == "full_end"
        return self._run(t)

    def on_half_end(self, half: int) -> list[Outcome]:
        """Half over; half windows close on the half_end event already in the stream."""
        return self._run(None)

    def on_full_end(self) -> list[Outcome]:
        """Full time: every question still open is closed and settles now."""
        self._ended = True
        return self._run(None)

    def _run(self, t: float | None) -> list[Outcome]:
        out = []
        for qid, cand in list(self._open.items()):
            result = settle_candidate(cand, self._since(cand), self._gen.bank,
                                      closed=self._ended)
            if result is None:
                continue
            del self._open[qid]
            self._gen.settled(qid)
            void = result == "void"
            at = t if t is not None else (self._keys[-1][1] if self._keys else None)
            out.append(Outcome(qid, "void" if void else "resolved", None if void else result,
                               (), at))
        return out
