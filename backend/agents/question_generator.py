"""question_generator.py — stage [2]: fill timing moments from Stephen's question bank.

Last Modified: 2026-10-26 16:00, first version: adapter from timing moments to backend/bank/
Inputs:  backend/bank/ (templates, select_for_slot, select_stack), the match being replayed
Outputs: ``Question`` per (moment, user); the bank ``Candidate`` behind each qid (for settling
         and probabilities)

Workflow:
  - Timing decides WHEN (a Moment); the bank decides WHAT. One decision per moment, cached.
  - Calm-play moments → ``select_for_slot`` (kind drawn 1/3 each: instant / window / player).
    In open play (ball live) the templates the match settles before a person can answer are
    left out (``agents/quiet.py``); at a dead ball every template is allowed.
  - Kickoff / half-time stack moments → ``select_stack``; the 2 forecast moments take the 2
    shared cards in moment-id order, the player moment takes each user's card.
  - Scheduled player moments and water breaks → player / window candidates directly.
  - Player cards are per user (``player_qid``); everyone else shares the moment id as qid.
  - Decisions are made for every roster player, not only the players picked in this room, so
    every room gets the same questions at the same second (and the LLM cache pre-warm covers
    every room).

CAUTION: the bank state is built from the replayed match up to the moment's second; events
after it are never passed in, so a choice cannot peek at the future.
"""

from __future__ import annotations

import bisect
import logging

from backend.app.models import Option, Question
from backend.bank.candidates import build_candidates
from backend.bank.loader import load_bank
from backend.bank.select import _player_candidates, select_for_slot, slot_rng
from backend.bank.stack import select_stack
from backend.bank.types import Candidate, MatchState
from backend.bank.types import Player as BankPlayer
from backend.core.types import DetectorState, Match, MatchEvent, Moment, Roster, player_qid

logger = logging.getLogger(__name__)

MAX_TONE_CHARS = 40
LABELS = {"yes": "Yes", "no": "No", "forward": "Forward", "back": "Back", "long": "Long",
          "short": "Short"}
STACK_PHASE = {"kickoff_stack": "kickoff", "ht_stack": "half_time"}
OPEN_PLAY = frozenset({"calm_play", "stalled_near_box"})  # moment situations with the ball live

def event_dict(e: MatchEvent) -> dict:
    """A ``MatchEvent`` as the contract-shaped dict the bank reads."""
    d = {"id": e.id, "t": e.t, "half": e.half, "team": e.team, "type": e.type,
         "player_id": e.player_id, "outcome": e.outcome}
    if e.start is not None:
        d["start"] = list(e.start)
    if e.end is not None:
        d["end"] = list(e.end)
    if e.related_player_id is not None:
        d["related_player_id"] = e.related_player_id
    return d


class BankGenerator:
    """Per-room question source over the bank. ``propose`` matches the app's ``ProposeFn``."""

    def __init__(self, match: Match, bank: dict | None = None) -> None:
        self.match = match
        self.bank = bank if bank is not None else load_bank()
        self.events = [event_dict(e) for e in match.events]
        self._keys = [(e.half, e.t) for e in match.events]
        r = match.roster
        self.players = {p.player_id: BankPlayer(p.player_id, p.name, p.team, p.position)
                        for p in r.players}
        self.team_names = {r.home: r.home_name, r.away: r.away_name}
        # moment id → ("shared", Candidate | None) or ("per_user", {player id: Candidate | None})
        self.decisions: dict[str, tuple[str, object]] = {}
        self.stacks: dict[str, object] = {}
        self._stack_fill: dict[str, int] = {}
        self.candidates: dict[str, Candidate] = {}  # qid → candidate behind the question
        self.open_template_ids: list[str] = []
        self.last_template_id: str | None = None

    # .........................................................................
    # bank state
    # .........................................................................

    def state(self, ds: DetectorState) -> MatchState:
        """Bank ``MatchState`` at the detector's second (events up to and including it)."""
        upto = bisect.bisect_right(self._keys, (ds.half, ds.t))
        r = self.match.roster
        open_windows = sum(1 for c in self.candidates.values() if c.kind == "window"
                           and c.template_id in self.open_template_ids)
        return MatchState(
            match_id=self.match.match_id, t=ds.t, half=ds.half, home=r.home, away=r.away,
            team_names=self.team_names, score={r.home: ds.score_home, r.away: ds.score_away},
            possession_team=ds.possession_team, events=self.events[:upto],
            open_template_ids=list(self.open_template_ids),
            last_template_id=self.last_template_id, open_window_count=open_windows,
            roster=self.players)

    def _users(self) -> dict[str, BankPlayer]:
        """Every roster player as a bank "user" (keyed by player id)."""
        return dict(sorted(self.players.items()))

    # .........................................................................
    # one decision per moment
    # .........................................................................

    def _decide(self, moment: Moment, ds: DetectorState, player_id: str | None):
        st = self.state(ds)
        if moment.kind in STACK_PHASE:
            key = moment.kind
            if key not in self.stacks:
                self.stacks[key] = select_stack(st, STACK_PHASE[key], self._users(),
                                                self.bank)
            stack = self.stacks[key]
            if stack is None:
                return ("none", None)
            if moment.category == "player":
                return ("per_user", dict(stack.per_user or {}))
            # the stack's forecast moments take the shared cards in the order they are filled
            idx = self._stack_fill.get(key, 0)
            self._stack_fill[key] = idx + 1
            return ("shared", stack.shared[idx] if idx < len(stack.shared) else None)
        if moment.kind == "player_instant":
            return ("per_user", {})  # filled lazily per user below
        exclude = self._exclude(moment)
        if moment.kind == "water_break":
            cands = build_candidates("window", st, self.bank, moment.open_at)
            rng = slot_rng(st.match_id, moment.open_at)
            return ("shared", rng.choice(cands) if cands else None)
        slot = select_for_slot(st, moment.open_at, self._users(), self.bank, exclude=exclude)
        if slot is None:
            return ("none", None)
        if slot.kind == "player":
            return ("per_user", dict(slot.per_user))
        return ("shared", slot.shared)

    @staticmethod
    def _exclude(moment: Moment) -> frozenset[str]:
        """Templates to leave out: those play settles too fast, while the ball is live."""
        if moment.situation not in OPEN_PLAY:
            return frozenset()
        from backend.agents.quiet import unsafe_in_open_play  # quiet imports this module
        return unsafe_in_open_play()

    def is_per_user(self, moment_id: str) -> bool:
        """True if the moment's cards differ per user (a missing card skips only that user)."""
        d = self.decisions.get(moment_id)
        return d is not None and d[0] == "per_user"

    def candidate_for(self, moment: Moment, ds: DetectorState,
                      player_id: str | None) -> Candidate | None:
        """The bank candidate this user sees for this moment, or None."""
        if moment.moment_id not in self.decisions:
            self.decisions[moment.moment_id] = self._decide(moment, ds, player_id)
        d = self.decisions[moment.moment_id]
        if d[0] != "per_user":
            return d[1]
        if player_id is None or player_id not in self.players:
            return None
        chosen = d[1]
        if player_id not in chosen:  # joined later, or a scheduled player moment
            found = _player_candidates(self.state(ds), self.bank, moment.open_at,
                                       self.players[player_id], exclude=self._exclude(moment))
            chosen[player_id] = slot_rng(self.match.match_id, moment.open_at).choice(found) \
                if found else None
        return chosen[player_id]

    # .........................................................................
    # ProposeFn
    # .........................................................................

    def propose(self, state: DetectorState, moment: Moment, user_player_id: str | None,
                roster: Roster | None = None) -> Question | None:
        """Fill one moment for one user from the bank, or None if nothing valid exists."""
        cand = self.candidate_for(moment, state, user_player_id)
        if cand is None:
            return None
        per_user = self.is_per_user(moment.moment_id)
        qid = player_qid(moment.moment_id, cand.player_id) if per_user else moment.moment_id
        if qid not in self.candidates:
            self.candidates[qid] = cand
            if cand.template_id not in self.open_template_ids:
                self.open_template_ids.append(cand.template_id)
            self.last_template_id = cand.template_id
        tone = moment.tone[:MAX_TONE_CHARS] if moment.tone else None
        r = self.match.roster
        names = {**LABELS, "home": r.home_name, "away": r.away_name}
        options = [Option(id=o, label=names.get(o, o)[:24]) for o in cand.options]
        return Question(
            qid=qid, category=cand.category, template_id=cand.template_id, text=cand.text,
            options=options, open_at=moment.open_at,
            lock_at=moment.lock_at, resolve_by=cand.resolve_by, hint_available=True,
            player_id=cand.player_id, params=dict(cand.params), tone=tone,
            situation=moment.situation, lock_rule=moment.lock_rule)

    def settled(self, qid: str) -> None:
        """A question settled: its template may be asked again."""
        cand = self.candidates.get(qid)
        if cand and cand.template_id in self.open_template_ids:
            self.open_template_ids.remove(cand.template_id)

    def probs(self, qid: str) -> dict[str, float] | None:
        """Shown-option probabilities from the bank's pricer, or None for unknown qids."""
        cand = self.candidates.get(qid)
        return dict(cand.p) if cand else None
