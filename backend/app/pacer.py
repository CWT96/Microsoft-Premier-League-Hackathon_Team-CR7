"""pacer.py — per-user pacer: which shared moment one user sees next, and when to nudge.

Last Modified: 2026-10-24 23:05, first version from SPEC section 4.6
Inputs:  open moments from the timing agent, answer and lock events for one user
Outputs: the next Moment for that user (or None) and a nudge text

Workflow:
  - next_card() picks the oldest unseen scheduled moment, else the newest unseen instant,
    with at least MIN_LOCK_MARGIN_S before lock, only when the user has no open card
  - two instants in a row locked unanswered pause the feed and queue one nudge text
  - resume() (feed.resume) restarts the feed; scheduled moments never count as ignored
  - lock_now() / locked_ids tell the pacer about early locks; it is safe without them too

CAUTION: one Pacer per connected user; the clock ``t`` is the displayed match clock.
"""

from __future__ import annotations

import logging
from typing import Iterable, Sequence

from backend.agents import timing_rules as R
from backend.core.types import Moment

logger = logging.getLogger(__name__)
_TIMED_KINDS = frozenset({"instant", "player_instant"})  # locked by match time


class Pacer:
    """Moves one user through the shared stream of moments at the user's own pace.

    Instants are delivered only while open with MIN_LOCK_MARGIN_S left. Scheduled moments
    (stacks, water break) are locked by the app in real time while the match
    clock is frozen, so they are delivered regardless of ``lock_at`` until ``lock_now``.
    """

    def __init__(self, user_id: str) -> None:
        self.user_id = user_id
        self._seen: set[str] = set()
        self._locked: set[str] = set()
        self._open: Moment | None = None
        self._unanswered = 0
        self._paused = False
        self._nudge_pending = False

    @property
    def paused(self) -> bool:
        return self._paused

    def next_card(self, t: float, half: int, open_moments: Sequence[Moment],
                  locked_ids: Iterable[str] = ()) -> Moment | None:
        """Moment to show this user now, or None.

        Args:
            t: displayed match clock in seconds.
            half: current half.
            open_moments: moments the app believes are open (any order).
            locked_ids: moment ids the app knows are locked (also remembered).

        Returns:
            The oldest unseen scheduled moment, else the newest unseen instant that is open
            in this half with at least ``MIN_LOCK_MARGIN_S`` before lock. None while the user
            has an open card or the feed is paused. An open instant card past its lock is
            first counted as an unanswered lock.
        """
        self._locked.update(locked_ids)
        if self._open is not None and self._open.kind in _TIMED_KINDS \
                and self._open.lock_at <= t:
            logger.warning("pacer %s: card %s passed its lock without on_lock",
                           self.user_id, self._open.moment_id)
            self.on_lock(self._open.moment_id, answered=False)
        if self._paused or self._open is not None:
            return None
        fresh = [m for m in open_moments
                 if m.moment_id not in self._seen and m.moment_id not in self._locked]
        timed = [m for m in fresh if m.kind in _TIMED_KINDS and m.half == half
                 and m.open_at <= t and m.lock_at - t >= R.MIN_LOCK_MARGIN_S]
        instants = [m for m in timed if m.kind == "instant"]
        scheduled = [m for m in fresh if m.kind not in _TIMED_KINDS] + [
            m for m in timed if m.kind == "player_instant"]
        if scheduled:
            pick = min(scheduled, key=lambda m: m.open_at)  # stable: list order breaks ties
        elif instants:
            pick = max(instants, key=lambda m: m.open_at)
        else:
            return None
        self._seen.add(pick.moment_id)
        self._open = pick
        return pick

    def lock_now(self, qids: Iterable[str]) -> None:
        """The app locked these moments (attack start, set piece taken, stack timeout).

        Locked moments are never handed out. An open card among them is closed without
        counting as ignored, since the user did not choose to skip it.
        """
        for qid in qids:
            self._locked.add(qid)
            if self._matches(qid):
                self._open = None

    def on_answer(self, qid: str, t: float) -> None:
        """The user answered ``qid``; the card closes and the unanswered streak resets."""
        if not self._matches(qid):
            self._log_mismatch("answer", qid)
            return
        self._open = None
        self._unanswered = 0

    def on_lock(self, qid: str, answered: bool) -> None:
        """``qid`` locked. Two unanswered instants in a row pause the feed."""
        if not self._matches(qid):
            self._log_mismatch("lock", qid)
            return
        card, self._open = self._open, None
        self._locked.add(card.moment_id)
        if answered:
            self._unanswered = 0
        elif card.kind == "instant":
            self._unanswered += 1
            if self._unanswered >= R.MAX_UNANSWERED_IN_ROW:
                self._paused = True
                self._nudge_pending = True

    def pending_nudge(self) -> str | None:
        """Nudge text; repeated until ``resume`` so a dropped delivery cannot strand the user."""
        return R.NUDGE_TEXT if self._nudge_pending else None

    def resume(self) -> None:
        """The user tapped the nudge (feed.resume); cards flow again."""
        self._paused = False
        self._unanswered = 0
        self._nudge_pending = False

    def _log_mismatch(self, what: str, qid: str) -> None:
        if self._open is not None:
            logger.warning("pacer %s: %s for %s but open card is %s", self.user_id, what, qid,
                           self._open.moment_id)

    def _matches(self, qid: str) -> bool:
        """True if ``qid`` is the open card (player cards use ``<moment id>-<player id>``)."""
        if self._open is None:
            return False
        return qid == self._open.moment_id or qid.startswith(self._open.moment_id + "-")
