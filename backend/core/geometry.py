"""geometry.py — the single definition of pitch zones and the frame flip.

Last Modified: 2026-10-25 00:10, first version: zone_of, in_final_third, flip_x, flip_xy
Inputs:  none
Outputs: pure functions on pitch coordinates in metres

Workflow:
  - ``zone_of(x)`` thirds in the acting team's attacking frame; final third is x >= 70
  - ``flip_xy`` / ``flip_x`` move a point between the two teams' frames
  - detector, model and resolver all import from here so the boundary cannot drift

CAUTION: none
"""

from __future__ import annotations

from backend.core.constants import (
    FINAL_THIRD_MIN_X, OWN_THIRD_MAX_X, PITCH_LENGTH_M, PITCH_WIDTH_M,
)
from backend.core.types import BallZone


def zone_of(x: float) -> BallZone:
    """Third of the pitch for ``x`` in the possession team's attacking frame."""
    if x < OWN_THIRD_MAX_X:
        return "own_third"
    return "final_third" if x >= FINAL_THIRD_MIN_X else "middle_third"


def in_final_third(x: float) -> bool:
    """True from x = 70 m (inclusive) in the attacking frame."""
    return zone_of(x) == "final_third"


def flip_x(x: float) -> float:
    """The same pitch point's x in the other team's frame."""
    return PITCH_LENGTH_M - x


def flip_xy(x: float, y: float) -> tuple[float, float]:
    """The same pitch point in the other team's frame."""
    return (PITCH_LENGTH_M - x, PITCH_WIDTH_M - y)
