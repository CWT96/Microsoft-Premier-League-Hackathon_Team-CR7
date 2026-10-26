"""xt_grid.py — fixed 12x8 expected-threat (xT) grid, rising towards the opponent goal.

Last Modified: 2026-10-24 23:10, first version with hand-set values
Inputs:  none
Outputs: ``xt(x, y)`` lookup used by the detector

Workflow:
  - 12 columns along the pitch length, 8 rows across the width, in the acting team's frame.
  - Values are hand-set (no training): they grow towards x = 105 and peak in the central rows.

CAUTION: none
"""

from __future__ import annotations

from backend.core.constants import PITCH_LENGTH_M, PITCH_WIDTH_M

GRID_COLS = 12
GRID_ROWS = 8

# Row 0 is y = 0 (touchline), column 0 is x = 0 (own goal line). Values in [0, 1].
XT_GRID: tuple[tuple[float, ...], ...] = (
    (0.003, 0.004, 0.006, 0.008, 0.012, 0.017, 0.024, 0.036, 0.072, 0.091, 0.160, 0.264),
    (0.004, 0.006, 0.008, 0.010, 0.015, 0.021, 0.030, 0.045, 0.091, 0.115, 0.203, 0.336),
    (0.005, 0.007, 0.010, 0.013, 0.019, 0.027, 0.039, 0.059, 0.117, 0.149, 0.261, 0.432),
    (0.007, 0.009, 0.012, 0.017, 0.023, 0.033, 0.047, 0.072, 0.143, 0.182, 0.319, 0.528),
    (0.007, 0.009, 0.012, 0.017, 0.023, 0.033, 0.047, 0.072, 0.143, 0.182, 0.319, 0.528),
    (0.005, 0.007, 0.010, 0.013, 0.019, 0.027, 0.039, 0.059, 0.117, 0.149, 0.261, 0.432),
    (0.004, 0.006, 0.008, 0.010, 0.015, 0.021, 0.030, 0.045, 0.091, 0.115, 0.203, 0.336),
    (0.003, 0.004, 0.006, 0.008, 0.012, 0.017, 0.024, 0.036, 0.072, 0.091, 0.160, 0.264),
)


def xt(x: float, y: float) -> float:
    """Expected threat of the zone containing (x, y), in the acting team's attacking frame.

    Args:
        x: Metres along the pitch, 0 = own goal line, 105 = opponent goal line.
        y: Metres across the pitch, 0..68.

    Returns:
        Zone value in [0, 1]. Positions off the pitch are clamped to the edge zone.
    """
    col = min(max(int(x / PITCH_LENGTH_M * GRID_COLS), 0), GRID_COLS - 1)
    row = min(max(int(y / PITCH_WIDTH_M * GRID_ROWS), 0), GRID_ROWS - 1)
    return XT_GRID[row][col]
