"""Resolver [4]: settles questions from events with the bank's primitives. Pure code, no LLM.

Public API: ``Resolver`` (``watcher.py``); the primitives live in ``primitives.py``.
"""

from backend.resolve.watcher import Resolver

__all__ = ["Resolver"]
