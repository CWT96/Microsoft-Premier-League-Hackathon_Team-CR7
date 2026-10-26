"""Resolver [4]: settles questions from events. Pure code, no LLM.

Public API: ``Resolver``, ``register``, ``Verdict``, ``RuleContext`` (see ``registry.py`` for
how to add a template).
"""

from backend.resolve import rules  # noqa: F401  (registers the 10 catalog templates)
from backend.resolve.engine import Resolver
from backend.resolve.registry import RuleContext, Verdict, register

__all__ = ["Resolver", "RuleContext", "Verdict", "register"]
