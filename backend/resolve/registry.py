"""registry.py — rule registry and the types a resolver rule works with.

Last Modified: 2026-10-24 22:00, first version.
Inputs:  none
Outputs: module-level ``RULES`` mapping template_id -> rule

Workflow:
  - A rule settles ONE question from the event log: ``rule(question, ctx) -> Verdict | None``.
    Return ``None`` while undecided; the engine calls the rule again on later seconds.
  - ``Verdict(option, evidence, facts, at)``: ``option=None`` means void.
  - Add a template in two steps: write the function, call ``register("my_template", fn)``
    (optionally ``validate=check_fn`` to reject bad params when the question is watched)
    at import time (``backend/resolve/rules.py`` does this for the 10 catalog templates).
  - The engine treats a question with ``resolve_by`` of "HT" or "FT" as a forecast: its rule
    is called only at half end / full end, with ``ctx.final`` set to "HT" or "FT". Any other
    ``resolve_by`` is evaluated every time new events arrive or a second passes.
  - ``ctx.after_lock()`` gives only events strictly after the lock; ``ctx.log`` is every event.
    Re-running a rule on the same log must give the same answer (amends re-run it).

CAUTION: none
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator, Literal

from backend.app.models import Question
from backend.core.types import MatchEvent, Roster, clock_key

Key = tuple[int, float]  # (half, displayed clock), see ``clock_key``


def event_key(e: MatchEvent) -> Key:
    """Sort key of an event on the match clock."""
    return clock_key(e.half, e.t)


@dataclass(frozen=True)
class Verdict:
    """A rule's decision. ``option=None`` is void."""

    option: str | None
    evidence: tuple[str, ...] = ()
    facts: dict[str, Any] = field(default_factory=dict)
    at: float | None = None  # clock of the deciding event or deadline


@dataclass(frozen=True)
class RuleContext:
    """Read-only view of the match for one question."""

    roster: Roster
    log: list[MatchEvent]  # all events sorted by (half, t); do not mutate
    split: int  # log[split:] is strictly after the lock
    lock: Key
    now: Key
    final: Literal["HT", "FT"] | None = None  # set when a forecast is being settled
    ended: bool = False  # the half or match ended while this question was still open

    def after_lock(self) -> list[MatchEvent]:
        """Events strictly after the lock, in clock order."""
        return self.log[self.split:]

    def before_lock(self) -> Iterator[MatchEvent]:
        """Events at or before the lock, latest first."""
        return (self.log[i] for i in range(self.split - 1, -1, -1))


Rule = Callable[[Question, RuleContext], "Verdict | None"]
Validator = Callable[[Question, Roster], None]

RULES: dict[str, Rule] = {}
VALIDATORS: dict[str, Validator] = {}


def register(template_id: str, rule: Rule, validate: Validator | None = None) -> None:
    """Add the rule for a template.

    Args:
        template_id: catalog id.
        rule: the settling function.
        validate: optional ``validate(question, roster)`` that raises ``ValueError`` when the
            question lacks what the rule needs. ``Resolver.watch`` calls it up front.

    Raises:
        ValueError: if the template already has a rule (guards against typos and clashes).
    """
    if template_id in RULES:
        raise ValueError(f"template {template_id!r} already registered")
    RULES[template_id] = rule
    if validate is not None:
        VALIDATORS[template_id] = validate


def get_validator(template_id: str) -> Validator | None:
    """The validator registered for a template, if any."""
    return VALIDATORS.get(template_id)


def get_rule(template_id: str) -> Rule:
    """Look up a rule.

    Raises:
        ValueError: if the template has no rule.
    """
    try:
        return RULES[template_id]
    except KeyError:
        raise ValueError(f"no resolver rule for template {template_id!r}") from None


def split_after(log: list[MatchEvent], lock: Key) -> int:
    """Index of the first event strictly after ``lock``."""
    return bisect_right(log, lock, key=event_key)
