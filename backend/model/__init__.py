"""Probability model [3]: top-2 options, AI hint, fair lines. Pure code, no LLM.

Public API: ``fit``, ``get_model``, ``option_probs``, ``top2``, ``hint``, ``fair_line``,
``register_prob`` (see ``registry.py`` for how to add a template).
"""

from backend.model.model import Model, cache_model, fit, get_model
from backend.model.probs import fair_line, hint, option_probs, top2
from backend.model.registry import register_prob

__all__ = ["Model", "cache_model", "fair_line", "fit", "get_model", "hint", "option_probs",
           "register_prob", "top2"]
