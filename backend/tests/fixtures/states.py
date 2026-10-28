"""states.py — shared DetectorState builder for tests (model, generator, timing, app).

Last Modified: 2026-10-25 00:20, first version
Inputs:  none
Outputs: DetectorState instances

Workflow:
  - ``make_state(**overrides)`` returns a calm first-half state: open play in the middle third,
    home team (NBR) in possession, no tracking, no pending restart, no attack
  - override any DetectorState field by keyword; unknown names raise TypeError

CAUTION: none
"""

from __future__ import annotations

from typing import Any

from backend.core.types import DetectorState

DEFAULTS: dict[str, Any] = dict(
    t=600.0, half=1, mode="live", break_kind=None, score_home=0, score_away=0,
    situation="open_play", dull_score=0.5, threat=0.05, movement=None, pressure_m=None,
    ball_xy=(50.0, 34.0), ball_zone="middle_third", possession_team="NBR",
    seconds_since_shot=100.0, last_attack_end_t=None, attack_active=False,
    restart_pending=False, restart_taken=False, dull_parts={"possession": 0.5},
)


def make_state(**overrides: Any) -> DetectorState:
    """A DetectorState with sane defaults (see module docstring).

    Example:
        >>> make_state(situation="throw_in", restart_pending=True).restart_pending
        True

    Args:
        **overrides: any ``DetectorState`` field. Dict defaults are copied, never shared.

    Returns:
        A frozen ``DetectorState``.
    """
    fields = {**DEFAULTS, "dull_parts": dict(DEFAULTS["dull_parts"]), **overrides}
    return DetectorState(**fields)
