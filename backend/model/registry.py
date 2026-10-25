"""registry.py — registry of probability functions, one per question template.

Last Modified: 2026-10-24 23:00, first version.
Inputs:  none
Outputs: module-level ``PROBS`` mapping template_id -> probability function

Workflow:
  - A probability function is ``fn(model, state, params) -> {option_id: p}``, summing to 1.
  - Add a template: write the function, call ``register_prob("my_template", fn)`` at import.
    ``backend/model/probs.py`` does this for the 10 catalog templates.
  - Mirrors ``backend/resolve/registry.py``: a template needs a rule there and a function here.

CAUTION: none
"""

from __future__ import annotations

from typing import Any, Callable

from backend.core.types import DetectorState

ProbFn = Callable[[Any, DetectorState, dict[str, Any]], dict[str, float]]

PROBS: dict[str, ProbFn] = {}


def register_prob(template_id: str, fn: ProbFn) -> None:
    """Add the probability function of a template.

    Args:
        template_id: catalog id.
        fn: ``fn(model, state, params) -> {option_id: probability}``.

    Raises:
        ValueError: if the template already has a function.
    """
    if template_id in PROBS:
        raise ValueError(f"template {template_id!r} already registered")
    PROBS[template_id] = fn


def get_prob(template_id: str) -> ProbFn:
    """Look up a probability function.

    Raises:
        ValueError: if the template has none.
    """
    try:
        return PROBS[template_id]
    except KeyError:
        raise ValueError(f"no probability function for template {template_id!r}") from None
