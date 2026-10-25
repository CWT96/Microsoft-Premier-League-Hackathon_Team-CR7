"""Settle a Candidate from later events, using the context it was asked in."""
from __future__ import annotations

from backend.bank.types import Candidate, Template
from backend.resolve.primitives import settle


def settle_candidate(candidate: Candidate, events: list[dict], bank: dict[str, Template],
                     closed: bool = False) -> str | None:
    """Return a shown option, "void", or None while the question is still open."""
    template = bank[candidate.template_id]
    return settle(template.resolve, events, candidate.window, candidate.context, closed,
                  candidate.params)
