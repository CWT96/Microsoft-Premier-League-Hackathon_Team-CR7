"""funnel.py — simulated ops funnel numbers for the enterprise dashboard.

Last Modified: 2026-10-24 22:16, noted that average_score is not in the funnel payload.
Inputs:  none
Outputs: none

Workflow:
  - Draw stage conversion rates from a seeded rng; counts shrink at every stage.
  - ``average_score`` is a separate helper and is NOT part of the ``/api/ops/funnel`` payload.

CAUTION: none
"""

from __future__ import annotations

from typing import Sequence

from backend.social._seed import make_rng

# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
# Constants
# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

SCANNED_RANGE = (4000, 9000)
ANSWERED_RATE = (0.55, 0.75)  # of scanned
SIGNED_UP_RATE = (0.30, 0.50)  # of answered_5plus
REDEEMED_RATE = (0.25, 0.45)  # of signed_up
NEW_MEMBER_RATE = (0.70, 0.90)  # of signed_up who were not members yet

# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
# Funnel
# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%


def ops_funnel(match_id: str, seed: int) -> dict[str, int]:
    """Simulated funnel for a match.

    Args:
        match_id: Match id (part of the seed).
        seed: Run seed.

    Returns:
        {scanned, answered_5plus, signed_up, redeemed, new_members}; each of the first four
        is at most the one before, and new_members is at most signed_up.
    """
    rng = make_rng("funnel", seed, match_id)
    scanned = rng.randint(*SCANNED_RANGE)
    answered = int(scanned * rng.uniform(*ANSWERED_RATE))
    signed_up = int(answered * rng.uniform(*SIGNED_UP_RATE))
    redeemed = int(signed_up * rng.uniform(*REDEEMED_RATE))
    new_members = int(signed_up * rng.uniform(*NEW_MEMBER_RATE))
    return {
        "scanned": scanned,
        "answered_5plus": answered,
        "signed_up": signed_up,
        "redeemed": redeemed,
        "new_members": new_members,
    }


def average_score(scores: Sequence[int]) -> float:
    """Mean final score.

    Args:
        scores: Final scores.

    Returns:
        The mean rounded to 1 decimal, or 0.0 for an empty list.
    """
    return round(sum(scores) / len(scores), 1) if scores else 0.0
