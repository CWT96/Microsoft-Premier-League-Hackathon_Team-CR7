"""bots.py — three rule-based bot friends that answer cards at their own pace.

Last Modified: 2026-10-24 22:15, first version.
Inputs:  none (hint probabilities are passed in by the caller)
Outputs: none

Workflow:
  - Each bot has a style, an engagement rate and an answer latency range.
  - ``Bot.decide`` returns (option or None, latency seconds); None means the card is skipped.
  - Draws come from an rng seeded by (seed, bot, qid), so results repeat exactly.

CAUTION: none
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Literal, Mapping

from backend.app.models import Question
from backend.social._seed import make_rng

# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
# Bots
# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

Style = Literal["follow_ai", "random", "contrarian"]

SAM_FOLLOW_AI = 0.85  # chance Sam takes the AI favourite
SAM_ENGAGEMENT = 0.95
SAM_LATENCY = (1.0, 3.0)
JO_ENGAGEMENT = 0.80
JO_LATENCY = (1.5, 5.0)
MO_ENGAGEMENT = 0.70  # Mo skips a lot more cards
MO_LATENCY = (3.5, 5.8)  # slow but steady: always inside the 6 s window
CONTRARIAN_PICK = 0.75  # chance Mo takes the AI underdog


@dataclass(frozen=True)
class Bot:
    """A simulated friend. ``seed`` makes its choices repeatable."""

    name: str
    style: Style
    engagement: float
    latency_range: tuple[float, float]
    seed: int

    def decide(
        self,
        question: Question,
        hint_probs: Mapping[str, float],
        rng: random.Random | None = None,
    ) -> tuple[str | None, float]:
        """Pick an option for ``question`` (needs ``.qid`` and ``.options[*].id``).

        Args:
            question: The open question.
            hint_probs: Model probability per option id (the AI pick is the larger).
            rng: Optional override; default is seeded from (bot seed, name, qid).

        Returns:
            (option id or None if the bot skips the card, answer latency in seconds).
        """
        rng = rng or make_rng(self.seed, self.name, question.qid)
        # Draw in a fixed order so each decision is independent of the branch taken.
        engaged = rng.random() < self.engagement
        latency = rng.uniform(*self.latency_range)
        roll = rng.random()
        pick_roll = rng.random()
        if not engaged:
            return None, latency
        ids = [o.id for o in question.options]
        favourite = max(ids, key=lambda i: hint_probs.get(i, 0.0))
        underdog = min(ids, key=lambda i: hint_probs.get(i, 0.0))
        if self.style == "follow_ai":
            return (favourite if roll < SAM_FOLLOW_AI else underdog), latency
        if self.style == "contrarian":
            return (underdog if roll < CONTRARIAN_PICK else favourite), latency
        return ids[int(pick_roll * len(ids))], latency


def make_bots(seed: int) -> list[Bot]:
    """Build the bot friends.

    Args:
        seed: Run seed.

    Returns:
        Sam (follows the AI), Jo (random) and Mo (contrarian).
    """
    return [
        Bot("Sam the superfan", "follow_ai", SAM_ENGAGEMENT, SAM_LATENCY, seed),
        Bot("Jo", "random", JO_ENGAGEMENT, JO_LATENCY, seed),
        Bot("Mo the maverick", "contrarian", MO_ENGAGEMENT, MO_LATENCY, seed),
    ]
