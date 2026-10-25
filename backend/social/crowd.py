"""crowd.py — answer splits (percentages) for the global, room and friends scopes.

Last Modified: 2026-10-24 22:16, splits are zero-filled over the question's option ids.
Inputs:  none
Outputs: none

Workflow:
  - Global: sample CROWD_SIZE simulated answers from the model's option probabilities,
    after adding noise, then convert to integer percentages (largest remainder).
  - Room / friends: count real answers.
  - Every split carries all option ids (0 when nobody chose one).
  - ``split_for_result`` keeps only scopes that have data.

CAUTION: none
"""

from __future__ import annotations

from typing import Mapping, Sequence

from backend.social._seed import make_rng

# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
# Constants
# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

CROWD_SIZE = 3000  # simulated answers per question
CROWD_NOISE = 0.08  # max shift of an option probability before sampling
TOTAL_PERCENT = 100

# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
# Splits
# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%


def _largest_remainder(weights: Mapping[str, float], option_ids: Sequence[str]) -> dict[str, int]:
    """Scale non-negative weights to integers summing to 100, zero-filled over option_ids."""
    total = sum(weights.values())
    if total <= 0:
        return {}
    exact = {k: weights.get(k, 0.0) * TOTAL_PERCENT / total for k in option_ids}
    floors = {k: int(x) for k, x in exact.items()}
    short = TOTAL_PERCENT - sum(floors.values())
    for k in sorted(exact, key=lambda k: (exact[k] - floors[k], k), reverse=True)[:short]:
        floors[k] += 1
    return floors


def crowd_split(
    qid: str, option_probs: Mapping[str, float], seed: int, option_ids: Sequence[str]
) -> dict[str, int]:
    """Simulated global split.

    Args:
        qid: Question id (part of the seed).
        option_probs: Model probability per option id.
        seed: Run seed.
        option_ids: All option ids of the question; each appears in the result.

    Returns:
        Integer percentages summing to 100.
    """
    rng = make_rng("crowd", seed, qid)
    ids = sorted(option_ids)
    noisy = [max(1e-6, option_probs.get(i, 0.0) + rng.uniform(-CROWD_NOISE, CROWD_NOISE))
             for i in ids]
    picks = rng.choices(ids, weights=noisy, k=CROWD_SIZE)
    return _largest_remainder({i: float(picks.count(i)) for i in ids}, option_ids)


def combine_split(answers: Mapping[str, str], option_ids: Sequence[str]) -> dict[str, int]:
    """Split of real answers.

    Args:
        answers: user -> chosen option id.
        option_ids: All option ids of the question; each appears in the result.

    Returns:
        Integer percentages summing to 100, or {} when there are no answers.
    """
    counts: dict[str, float] = {}
    for option in answers.values():
        counts[option] = counts.get(option, 0.0) + 1.0
    return _largest_remainder(counts, option_ids)


def split_for_result(
    qid: str,
    option_probs: Mapping[str, float],
    seed: int,
    option_ids: Sequence[str],
    room_answers: Mapping[str, str] | None = None,
    friends_answers: Mapping[str, str] | None = None,
) -> dict[str, dict[str, int]]:
    """Contract ``crowd_split`` object.

    Args:
        qid: Question id.
        option_probs: Model probability per option id (empty skips the global scope).
        seed: Run seed.
        option_ids: All option ids of the question.
        room_answers: user -> option for the room scope, if any.
        friends_answers: bot/friend -> option for the friends scope, if any.

    Returns:
        Only the scopes that have data.
    """
    scopes = {
        "global": crowd_split(qid, option_probs, seed, option_ids) if option_probs else {},
        "room": combine_split(room_answers or {}, option_ids),
        "friends": combine_split(friends_answers or {}, option_ids),
    }
    return {name: split for name, split in scopes.items() if split}
