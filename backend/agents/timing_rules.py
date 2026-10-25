"""timing_rules.py — every tunable constant of the timing agent [2b] and the pacer.

Last Modified: 2026-10-24 22:30, first version from SPEC section 4
Inputs:  none
Outputs: constants only

Workflow:
  - cost weights and ramps (SPEC 4.3), gate values (4.4), scheduled slots (4.7)
  - override costs per situation, tone lists per moment situation

CAUTION: none
"""

from __future__ import annotations

# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
#
# Interruption cost (SPEC 4.3)
#
# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

# Signal weights. Movement and pressure drop out (weights renormalised) without tracking.
SIGNAL_WEIGHTS = {
    "zone": 0.25, "threat": 0.25, "movement": 0.20,
    "pressure": 0.10, "shot_gap": 0.10, "context": 0.10,
}

ZONE_SCORES = {"own_third": 0.0, "middle_third": 0.4, "final_third": 1.0}

# Ramps: (value giving score 0, value giving score 1); linear in between, clipped.
THREAT_RAMP = (0.05, 0.20)  # max xT in the last 10 s
PRESSURE_RAMP_M = (8.0, 3.0)  # carrier-to-opponent distance in metres
SHOT_GAP_RAMP_S = (120.0, 20.0)  # seconds since the last shot

# Match context: tense end of a half with a close score raises the cost.
CONTEXT_TENSE_WINDOW_S = 600.0  # last 10 minutes of a half
CONTEXT_CLOSE_GOALS = 1  # score gap this small is "close"
CONTEXT_BIG_LEAD_GOALS = 3
CONTEXT_EARLY_S = 300.0  # first 5 minutes of a half
CONTEXT_TENSE = 1.0
CONTEXT_NEUTRAL = 0.3
CONTEXT_RELAXED = 0.0

# Override costs (SPEC 4.3 table).
COST_SILENT = 1.0
COST_LIVE_SET_PIECE = 0.9  # ball live in the box after the kick
COST_SET_PIECE_WAIT = 0.2
COST_REFEREE = 0.1
COST_STALLED_NEAR_BOX = 0.4
COST_STOPPAGE_RESTART = 0.1  # throw-in, goal kick, far free kick
COST_BREAK_IN_PLAY = 0.0  # substitution, injury
COST_AFTER_GOAL = 0.2
GOAL_SILENT_S = 8.0  # celebrate first

# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
#
# Gate (SPEC 4.4)
#
# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

CANDIDATE_COST = 0.45  # at or below: the match can spare the viewer
SILENT_COST = 0.70  # at or above: silent, and stay silent until CANDIDATE_COST
MIN_SPACING_S = 4.0
AFTER_ATTACK_S = 8.0
HALF_END_GUARD_S = 15.0
CARD_WINDOW_S = 8.0  # open_at -> lock_at for instants
STACK_WINDOW_S = 60.0  # kickoff and half-time stacks
WATER_BREAK_WINDOW_S = 30.0
HOLD_DELAY_S = 1.0
MAX_HOLDS_IN_ROW = 2
NEAR_BOX_MIN_X = 70.0  # a free kick from x >= 70 (attacking frame) is a set piece
RECENT_SITUATIONS = 3
WALKING_MOVEMENT = 0.3  # below this, players are walking or standing

# Rules that are routine pacing and do not get a "blocked" decision.
QUIET_BLOCK_RULES = frozenset({"spacing"})

# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
#
# Scheduled moments (SPEC 4.7)
#
# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

PLAYER_SLOTS = ((1, 2400.0), (2, 4200.0))  # (half, t): 40:00 and 70:00
STACK_CATEGORIES = ("forecast", "forecast", "player")

# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
#
# Director policy
#
# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

POLICY_AGENT = "timing"
POLICY_TIMEOUT_S = 1.0
TONE_MAX_CHARS = 40
REASON_MAX_CHARS = 200

# Tones carry no digits (number guard).
TONES = {
    "calm_play": ("Quiet moment. Quick one!", "Play is calm. Your call!", "Slow patch. Try this!"),
    "stalled_near_box": ("Pressing, no shot yet.", "Edge of the box. Quick pick!"),
    "throw_in": ("Throw-in. Quick one!", "Ball is out. Your call!"),
    "goal_kick": ("Goal kick. Quick one!", "Reset time. Your call!"),
    "set_piece_wait": ("Set piece. Make your pick!", "Everyone is waiting. Pick now!"),
    "referee_decision": ("Whistle! Quick pick!", "The referee is deciding."),
    "substitution": ("Fresh legs. Quick one!", "Substitution. Your call!"),
    "injury": ("Play is paused. Your call!", "Treatment on the pitch."),
    "water_break": ("Drinks break!",),
    "after_goal": ("What a goal! What next?", "Back to it. Your call!"),
    "kickoff": ("Kickoff!",),
    "half_time": ("Half time!",),
}

# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
#
# Pacer (SPEC 4.6)
#
# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

MIN_LOCK_MARGIN_S = 5.0  # never deliver a moment with less than this left
MAX_UNANSWERED_IN_ROW = 2
NUDGE_TEXT = "Still with us? Tap for the next one."
