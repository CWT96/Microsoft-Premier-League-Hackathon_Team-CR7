"""Hard-coded rate priors (spec section 7). Not computed from the replayed dataset."""
from __future__ import annotations

MATCH_RATES_90: dict[str, float] = {
    "goal": 2.7, "shot": 25, "shot_on_target": 9, "card": 4,
    "penalty": 0.3, "foul": 22, "save": 6,
}
TEAM_GOALS_90 = 1.35

POSITION_RATES_90: dict[str, dict[str, float]] = {
    "GK": {"touch": 35, "save": 3, "clearance": 0, "pass_complete": 25,
           "shot": 0, "win_ball": 0, "goal": 0},
    "DF": {"touch": 60, "save": 0, "clearance": 4, "pass_complete": 40,
           "shot": 0.5, "win_ball": 3, "goal": 0.05},
    "MF": {"touch": 70, "save": 0, "clearance": 1, "pass_complete": 45,
           "shot": 1.2, "win_ball": 2.5, "goal": 0.15},
    "FW": {"touch": 40, "save": 0, "clearance": 0.3, "pass_complete": 20,
           "shot": 2.5, "win_ball": 0.8, "goal": 0.4},
}

INSTANT_DEFAULTS: dict[str, dict[str, float]] = {
    "next_pass_dir": {"backward": 0.44, "forward": 0.41, "sideways": 0.15},
    "keep_ball": {"yes": 0.7, "no": 0.3},
    "reach_final_third": {"yes": 0.3, "no": 0.7},
    "shot_soon": {"yes": 0.2, "no": 0.8},
    "next_pass_complete": {"yes": 0.8, "no": 0.2},
    "next_pass_long": {"long": 0.25, "short": 0.75},
    "switch_play": {"yes": 0.3, "no": 0.7},
    "back_to_keeper": {"yes": 0.3, "no": 0.7},
}

PLAYER_NEXT_PASS: dict[str, dict[str, float]] = {
    "complete": {"GK": 0.75, "DF": 0.85, "MF": 0.82},
    "long": {"GK": 0.55, "DF": 0.30, "MF": 0.25},
}
