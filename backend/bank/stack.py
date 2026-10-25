"""Question stacks at kickoff and half time: 2 shared window cards plus 1 player card each."""
from __future__ import annotations

import hashlib
import random
from collections import defaultdict
from dataclasses import replace

from backend.bank.candidates import build_candidates
from backend.bank.select import _player_candidates
from backend.bank.types import Candidate, MatchState, Phase, Player, StackDecision, Template

_RESTART = {"kickoff": (0.0, 1), "half_time": (2700.0, 2)}
# Nobody has touched the ball yet, and stack questions do not use up the open-window limit.
_SKIP = frozenset({"max_open_windows", "player_active"})
_SHARED_CARDS = 2


def stack_rng(match_id: str, phase: Phase) -> random.Random:
    """Random generator seeded from the match and phase, so replays repeat exactly."""
    digest = hashlib.sha256(f"{match_id}:{phase}".encode()).hexdigest()
    return random.Random(int(digest[:16], 16))


def restart_state(state: MatchState, phase: Phase) -> MatchState:
    """The match as it is at the restart: clock reset, nothing open, nobody in possession."""
    t, half = _RESTART[phase]
    return replace(state, t=t, half=half, possession_team=None, open_window_count=0,
                   open_template_ids=[], last_template_id=None)


def select_stack(state: MatchState, phase: Phase, users: dict[str, Player],
                 bank: dict[str, Template]) -> StackDecision | None:
    """Choose the stack for this restart, or None if there is nothing to ask."""
    rs = restart_state(state, phase)
    open_at = rs.t
    rng = stack_rng(state.match_id, phase)
    opts = dict(serve_lag=0.0, skip_guards=_SKIP, phase=phase)

    by_id: dict[str, list[Candidate]] = defaultdict(list)
    for c in build_candidates("window", rs, bank, open_at, **opts):
        by_id[c.template_id].append(c)
    ids = sorted(by_id)
    shared = [rng.choice(by_id[i]) for i in rng.sample(ids, min(_SHARED_CARDS, len(ids)))]

    per_user: dict[str, list[Candidate]] = {
        uid: _player_candidates(rs, bank, open_at, users[uid], **opts) for uid in sorted(users)
    }
    chosen = ({uid: rng.choice(c) for uid, c in per_user.items()}
              if per_user and all(per_user.values()) else None)
    if not shared and chosen is None:
        return None
    return StackDecision(phase, open_at, shared, chosen)
