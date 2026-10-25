"""engine.py — the Resolver: watches questions, ingests events, returns Outcomes.

Last Modified: 2026-10-24 22:00, first version.
Inputs:  Question + actual lock time (``watch``), events per second (``step``)
Outputs: ``Outcome`` lists; a question is returned once, and again if an amend changes it

Workflow:
  - ``watch`` registers a question with its rule from ``registry``.
  - ``step`` merges new events into one sorted log (an event with ``amends`` replaces the
    event it points to), re-resolves settled questions the amend touched, then runs the
    rule of every open continuous question.
  - ``on_half_end`` / ``on_full_end`` settle forecasts ("HT" / "FT") and cut short
    continuous questions still open.

CAUTION: only events strictly after the lock count. Match clock order is (half, t).
"""

from __future__ import annotations

import logging
from bisect import insort
from dataclasses import dataclass
from typing import Literal, Sequence

from backend.app.models import Question
from backend.core.constants import SECOND_HALF_START_S
from backend.core.types import MatchEvent, Outcome, Roster, clock_key
from backend.resolve.registry import (Key, Rule, RuleContext, Verdict, event_key, get_rule,
                                      get_validator, split_after)

FORECAST_MARKS = ("HT", "FT")
RULE_ERRORS = (ValueError, KeyError, TypeError, IndexError)  # a broken rule must not stop a tick

logger = logging.getLogger(__name__)


@dataclass
class _Watch:
    question: Question
    lock: Key
    rule: Rule

    @property
    def is_forecast(self) -> bool:
        return self.question.resolve_by in FORECAST_MARKS


@dataclass
class _Settled:
    watch: _Watch
    outcome: Outcome
    now: Key
    final: Literal["HT", "FT"] | None
    ended: bool


class Resolver:
    """Settles questions from the event stream. Pure code; no LLM."""

    def __init__(self, roster: Roster) -> None:
        self._roster = roster
        self._log: list[MatchEvent] = []
        self._by_id: dict[str, MatchEvent] = {}
        self._open: dict[str, _Watch] = {}
        self._settled: dict[str, _Settled] = {}
        self._now: Key | None = None
        self._seen: set[str] = set()  # every event id ever delivered (incl. amended-away)

    # .........................................................................
    # Public API
    # .........................................................................

    def watch(self, question: Question, lock_t: float, lock_half: int | None = None) -> None:
        """Start resolving ``question``; only events strictly after the lock count.

        Args:
            question: the published question.
            lock_t: ACTUAL lock time (for ``on_restart``, when the set piece was taken).
            lock_half: half of the lock; default is the half of the latest ``step``.

        Raises:
            ValueError: unknown template, bad params, or qid already watched.
        """
        if question.qid in self._open or question.qid in self._settled:
            raise ValueError(f"question {question.qid!r} already watched")
        rule = get_rule(question.template_id)
        check = get_validator(question.template_id)
        if check is not None:
            check(question, self._roster)
        if lock_half is None:
            lock_half = (self._now[0] if self._now is not None
                         else 1 if lock_t < SECOND_HALF_START_S else 2)
        self._open[question.qid] = _Watch(question, clock_key(lock_half, lock_t), rule)

    def step(self, half: int, t: float, new_events: Sequence[MatchEvent]) -> list[Outcome]:
        """Advance the clock to (half, t) with the events that arrived; return new Outcomes."""
        self._now = max(self._now or (half, t), (half, t))
        replaced = self._ingest(new_events)
        out = self._reresolve(replaced)
        for qid, w in list(self._open.items()):
            if w.is_forecast:
                continue
            outcome = self._evaluate(w, final=None, ended=False)
            if outcome is not None:
                out.append(outcome)
        return out

    def on_half_end(self, half: int) -> list[Outcome]:
        """Half over: settle "HT" forecasts (first half only), void/close open instants."""
        return self._close(half, final="HT" if half == 1 else None)

    def on_full_end(self) -> list[Outcome]:
        """Full time: settle every forecast still open and close open instants."""
        return self._close(None, final="FT")

    # .........................................................................
    # Internals
    # .........................................................................

    def _ctx(self, w: _Watch, now: Key, final: Literal["HT", "FT"] | None,
             ended: bool) -> RuleContext:
        return RuleContext(self._roster, self._log, split_after(self._log, w.lock), w.lock, now,
                           final, ended)

    def _run(self, w: _Watch, now: Key, final: Literal["HT", "FT"] | None,
             ended: bool) -> Outcome | None:
        verdict: Verdict | None = w.rule(w.question, self._ctx(w, now, final, ended))
        if verdict is None:
            return None
        status = "resolved" if verdict.option is not None else "void"
        return Outcome(w.question.qid, status, verdict.option, verdict.evidence, verdict.at,
                       dict(verdict.facts))

    def _evaluate(self, w: _Watch, final: Literal["HT", "FT"] | None, ended: bool
                  ) -> Outcome | None:
        assert self._now is not None
        try:
            outcome = self._run(w, self._now, final, ended)
        except RULE_ERRORS as exc:  # void the faulty question; keep the others flowing
            logger.warning("rule for %s (%s) failed, voiding: %r", w.question.qid,
                           w.question.template_id, exc)
            outcome = Outcome(w.question.qid, "void", None, (), self._now[1], {})
        if outcome is not None:
            del self._open[w.question.qid]
            self._settled[w.question.qid] = _Settled(w, outcome, self._now, final, ended)
        return outcome

    def _close(self, half: int | None, final: Literal["HT", "FT"] | None) -> list[Outcome]:
        out: list[Outcome] = []
        for w in list(self._open.values()):
            if w.is_forecast:
                if w.question.resolve_by == "HT" and final is None:
                    continue
                if w.question.resolve_by == "FT" and final != "FT":
                    continue
                outcome = self._evaluate(w, final=w.question.resolve_by, ended=True)
            elif half is None or w.lock[0] == half:
                outcome = self._evaluate(w, final=None, ended=True)
            else:
                continue
            if outcome is not None:
                out.append(outcome)
        return out

    def _ingest(self, events: Sequence[MatchEvent]) -> list[tuple[MatchEvent, MatchEvent]]:
        """Merge events into the log. Returns (old, new) pairs for amends of known events."""
        replaced: list[tuple[MatchEvent, MatchEvent]] = []
        for e in events:
            if e.id in self._seen:
                continue
            self._seen.add(e.id)
            old = self._by_id.get(e.amends) if e.amends else None
            if old is not None:
                self._log.remove(old)
                del self._by_id[old.id]
                replaced.append((old, e))
            elif e.amends:
                logger.warning("event %s amends unknown event %s; applied as new", e.id, e.amends)
            insort(self._log, e, key=event_key)
            self._by_id[e.id] = e
        return replaced

    def _reresolve(self, replaced: list[tuple[MatchEvent, MatchEvent]]) -> list[Outcome]:
        """Re-run settled questions an amend touched; return those whose Outcome changed or
        whose evidence used the amended event, once per qid. If a re-run is undecided or
        fails, the old result stands."""
        out: dict[str, Outcome] = {}
        for old, new in replaced:
            for qid, s in self._settled.items():
                used = old.id in s.outcome.evidence
                keys = (event_key(old), event_key(new))
                in_window = any(s.watch.lock < k <= s.now for k in keys)
                if not (used or in_window or s.watch.is_forecast):
                    continue
                try:
                    outcome = self._run(s.watch, s.now, s.final, s.ended)
                except RULE_ERRORS as exc:
                    logger.warning("re-resolve of %s failed, keeping old result: %r", qid, exc)
                    continue
                if outcome is None:
                    continue
                changed = outcome != s.outcome or outcome.facts != s.outcome.facts
                s.outcome = outcome
                if used or changed:
                    out[qid] = outcome
        return list(out.values())
