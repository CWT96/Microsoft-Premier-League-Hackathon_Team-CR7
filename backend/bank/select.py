"""Pick the question for a dull slot: seeded kind draw, candidates, optional LLM picker."""
from __future__ import annotations

import hashlib
import random
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from backend.bank.candidates import build_candidates
from backend.bank.types import Candidate, MatchState, Player, SlotDecision, Template

Picker = Callable[[list[Candidate], MatchState], "str | None"]

_KINDS = ["instant", "window", "player"]


def slot_rng(match_id: str, open_at: float) -> random.Random:
    """Random generator seeded from the match and slot time, so replays repeat exactly."""
    digest = hashlib.sha256(f"{match_id}:{int(open_at)}".encode()).hexdigest()
    return random.Random(int(digest[:16], 16))


def _player_candidates(state: MatchState, bank: dict[str, Template], open_at: float,
                       player: Player, **opts) -> list[Candidate]:
    """Candidates for the player, else a touch question about a same-position teammate.

    opts are passed to build_candidates (stacks use them for serve_lag, skip_guards, phase).
    """
    cands = build_candidates("player", state, bank, open_at, player, **opts)
    if cands:
        return cands
    for mate in state.roster.values():
        if (mate.player_id != player.player_id and mate.team == player.team
                and mate.position == player.position):
            found = [c for c in build_candidates("player", state, bank, open_at, mate, **opts)
                     if c.template_id == "player_touch_window"]
            if found:
                return found
    return []


def _ask_picker(picker: Picker, cands: list[Candidate], state: MatchState,
                timeout_s: float) -> str | None:
    """Run the picker with a timeout; any failure gives None. Never waits on a hung thread."""
    executor = ThreadPoolExecutor(max_workers=1)
    try:
        return executor.submit(picker, cands, state).result(timeout=timeout_s)
    except Exception:  # timeout, picker error: fall back to the seeded choice
        return None
    finally:
        executor.shutdown(wait=False)


def select_for_slot(state: MatchState, open_at: float, users: dict[str, Player],
                    bank: dict[str, Template], picker: Picker | None = None,
                    timeout_s: float = 2.0,
                    exclude: frozenset[str] = frozenset()) -> SlotDecision | None:
    """Choose what to ask in this slot, or None if nothing valid exists.

    exclude: template ids the caller rules out for this slot (the kind draw is unchanged).
    """
    rng = slot_rng(state.match_id, open_at)
    kind = rng.choice(_KINDS)

    if kind == "player":
        per_user: dict[str, list[Candidate]] = {
            uid: _player_candidates(state, bank, open_at, users[uid], exclude=exclude)
            for uid in sorted(users)
        }
        if per_user and all(per_user.values()):
            chosen = {uid: rng.choice(c) for uid, c in per_user.items()}
            return SlotDecision(open_at, "player", None, chosen)
        kind = "instant"

    cands = build_candidates(kind, state, bank, open_at, exclude=exclude)
    if not cands and kind == "window":
        kind = "instant"
        cands = build_candidates(kind, state, bank, open_at, exclude=exclude)
    if not cands:
        return None

    pick = None
    if picker is not None:
        key = _ask_picker(picker, cands, state, timeout_s)
        pick = next((c for c in cands if c.key == key), None)
    if pick is None:
        pick = rng.choice(cands)
    return SlotDecision(open_at, kind, pick, None)
