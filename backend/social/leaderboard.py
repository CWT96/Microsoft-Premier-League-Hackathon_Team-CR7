"""leaderboard.py — top-10-plus-you leaderboards and simulated global scores.

Last Modified: 2026-10-24 22:16, added simulated_global_entries and typed scope.
Inputs:  none
Outputs: none

Workflow:
  - ``leaderboard`` sorts by final score, keeps the top N and appends "you" if outside it.
  - ``simulated_global_scores`` draws a Beta-skewed 0-1000 crowd for the global rank.
  - ``simulated_global_entries`` pairs those scores with fictional display names.

CAUTION: none
"""

from __future__ import annotations

from typing import Any, Literal, Sequence

from backend.social._seed import make_rng

# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
# Constants
# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

Scope = Literal["room", "friends", "global"]

TOP_N = 10
MAX_SCORE = 1000
GLOBAL_PLAYERS = 3000
BETA_A, BETA_B = 3.0, 2.0  # mean 0.6: typical players land mid-to-high
FIRST_NAMES = (
    "Alex", "Bea", "Cam", "Dani", "Eli", "Faye", "Gus", "Hana", "Ivy", "Jude", "Kai", "Lena",
    "Milo", "Nia", "Omar", "Pia", "Quinn", "Rosa", "Sol", "Tess", "Uma", "Vic", "Wren", "Zed",
)
NAME_SUFFIXES = ("", "_77", "_fc", "99", "_x", ".k", "_09", "_gg")

# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
# Leaderboards
# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%


def leaderboard(
    scope: Scope,
    entries: Sequence[tuple[str, int, bool]],
    top: int = TOP_N,
) -> dict[str, Any]:
    """Contract ``leaderboard`` payload.

    Args:
        scope: "room", "friends" or "global".
        entries: (display_name, final_score, is_you) for everyone in the scope.
        top: How many entries to show before appending "you".

    Returns:
        {scope, entries: [{display_name, final_score, is_you}]}: the top entries, plus
        "you" if outside them.
    """
    # "you" wins ties on purpose: it can only help the player's displayed rank.
    ranked = sorted(entries, key=lambda e: (-e[1], not e[2], e[0]))
    shown = ranked[:top] + [e for e in ranked[top:] if e[2]]
    return {
        "scope": scope,
        "entries": [
            {"display_name": n, "final_score": int(s), "is_you": bool(y)} for n, s, y in shown
        ],
    }


def simulated_global_scores(n: int = GLOBAL_PLAYERS, seed: int = 0) -> list[int]:
    """Plausible global final scores.

    Args:
        n: Number of simulated players.
        seed: Run seed.

    Returns:
        n integer scores in 0-1000, skewed towards the upper-middle.
    """
    rng = make_rng("global_scores", seed)
    return [round(rng.betavariate(BETA_A, BETA_B) * MAX_SCORE) for _ in range(n)]


def simulated_global_entries(n: int = GLOBAL_PLAYERS, seed: int = 0) -> list[tuple[str, int]]:
    """Simulated global players with fictional display names.

    Args:
        n: Number of simulated players.
        seed: Run seed.

    Returns:
        (display_name, final_score) pairs; names are unique within the list.
    """
    rng = make_rng("global_names", seed)
    scores = simulated_global_scores(n, seed)
    return [
        (f"{rng.choice(FIRST_NAMES)}{rng.choice(NAME_SUFFIXES)}#{i + 1}", s)
        for i, s in enumerate(scores)
    ]
