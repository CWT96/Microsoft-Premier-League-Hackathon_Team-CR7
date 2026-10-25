"""Pitch, clock and match constants shared by every stage.

Every tunable threshold of a single stage lives in that stage's own rules module
(e.g. ``backend/agents/timing_rules.py``); only facts about football and the clock live here.
"""

PITCH_LENGTH_M = 105.0
PITCH_WIDTH_M = 68.0
GOAL_CENTRE = (105.0, 34.0)  # in the acting team's attacking frame

# Thirds, in the acting team's attacking frame (attacks left to right).
OWN_THIRD_MAX_X = 35.0
FINAL_THIRD_MIN_X = 70.0

# Displayed clock (seconds). The second half starts at 2700 even after stoppage time.
HALF_LENGTH_S = 2700.0
SECOND_HALF_START_S = 2700.0
NOMINAL_HALF_END_S = {1: 2700.0, 2: 5400.0}

DEFAULT_MATCH_ID = "nbr-cal-seed42"

RESTART_TYPES = frozenset({"throw_in", "goal_kick", "corner", "free_kick", "penalty"})
STOPPAGE_TYPES = frozenset({"substitution", "injury", "var_check"})
