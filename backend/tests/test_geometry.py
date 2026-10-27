"""test_geometry.py — the final-third boundary (x = 70) in the geometry helpers and the detector.

Last Modified: 2026-10-25 00:15, first version
Inputs:  none
Outputs: none

Workflow:
  - unit tests for the geometry helpers
  - the same x = 70.0 / 69.99 decides the detector zone (the bank's reach_final_third uses its
    own end_x_gt filter in backend/bank/templates.json)

CAUTION: none
"""

from __future__ import annotations

import pytest

from backend.core import geometry
from backend.core.constants import FINAL_THIRD_MIN_X, OWN_THIRD_MAX_X
from backend.core.types import MatchEvent
from backend.detect.detector import Detector
from backend.tests.test_detect import ROSTER


@pytest.mark.parametrize("x,zone", [
    (0.0, "own_third"), (OWN_THIRD_MAX_X - 0.01, "own_third"),
    (OWN_THIRD_MAX_X, "middle_third"), (FINAL_THIRD_MIN_X - 0.01, "middle_third"),
    (FINAL_THIRD_MIN_X, "final_third"), (105.0, "final_third")])
def test_zone_of(x, zone):
    assert geometry.zone_of(x) == zone
    assert geometry.in_final_third(x) == (zone == "final_third")


def test_flip_is_an_involution_and_mirrors_both_axes():
    assert geometry.flip_xy(20.0, 10.0) == (85.0, 58.0)
    assert geometry.flip_xy(*geometry.flip_xy(33.3, 12.5)) == pytest.approx((33.3, 12.5))
    assert geometry.flip_x(70.0) == 35.0


@pytest.mark.parametrize("x,expected", [(70.0, "final_third"), (69.99, "middle_third")])
def test_boundary_in_detector(x, expected):
    det = Detector(ROSTER, "NBR", "CAL")
    ev = MatchEvent(id="e1", t=1.2, half=1, team="NBR", type="pass", start=(x - 1, 30.0),
                    end=(x, 30.0), outcome="complete")
    state = det.step(1, 1.0, [ev])
    assert state.ball_zone == expected
