"""_seed.py — stable string-keyed seeding for the social layer.

Last Modified: 2026-10-24 22:15, first version.
Inputs:  none
Outputs: none

Workflow:
  - Hash the parts with SHA-256 (Python's str hash is randomised per process).

CAUTION: none
"""

from __future__ import annotations

import hashlib
import random


# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
# Seeding
# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%


def make_rng(*parts: object) -> random.Random:
    """Return a seeded rng.

    Args:
        *parts: Values joined (with a unit separator) into the seed.

    Returns:
        A ``random.Random`` that is identical across processes for the same parts.
    """
    digest = hashlib.sha256("\x1f".join(str(p) for p in parts).encode()).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))
