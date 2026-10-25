"""Hard-coded rate priors (spec section 7). Not computed from the replayed dataset."""
from __future__ import annotations

MATCH_RATES_90: dict[str, float] = {
    "goal": 2.7, "shot": 25, "shot_on_target": 9, "card": 3.5,
    "penalty": 0.3, "foul": 22, "save": 7,
}
TEAM_GOALS_90 = 1.35

POSITION_RATES_90: dict[str, dict[str, float]] = {
    "GK": {"touch": 35, "save": 3, "clearance": 0, "pass_complete": 25,
           "shot": 0, "win_ball": 0, "goal": 0},
    "DF": {"touch": 60, "save": 0, "clearance": 4, "pass_complete": 40,
           "shot": 0.5, "win_ball": 2.0, "goal": 0.05},
    "MF": {"touch": 70, "save": 0, "clearance": 1, "pass_complete": 45,
           "shot": 1.2, "win_ball": 2.5, "goal": 0.15},
    "FW": {"touch": 40, "save": 0, "clearance": 0.3, "pass_complete": 20,
           "shot": 2.5, "win_ball": 0.8, "goal": 0.4},
}

# Measured inside slow spells of real matches (team review of PR #6).
INSTANT_DEFAULTS: dict[str, dict[str, float]] = {
    "next_pass_dir": {"backward": 0.44, "forward": 0.41, "sideways": 0.15},
    "keep_ball": {"yes": 0.6, "no": 0.4},
    "reach_final_third": {"yes": 0.65, "no": 0.35},
    "shot_soon": {"yes": 0.11, "no": 0.89},
    "next_pass_complete": {"yes": 0.94, "no": 0.06},
    "next_pass_long": {"long": 0.15, "short": 0.85},
    "switch_play": {"yes": 0.11, "no": 0.89},
    "back_to_keeper": {"yes": 0.15, "no": 0.85},
}

PLAYER_NEXT_PASS: dict[str, dict[str, float]] = {
    "forward": {"DF": 0.55, "MF": 0.45},
    "long": {"GK": 0.55, "DF": 0.30, "MF": 0.25},
}
