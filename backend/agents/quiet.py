"""quiet.py — which bank templates the match settles before a person can answer, in open play.

Last Modified: 2026-10-26 17:30, first version
Inputs:  every match in data/source/ (ingest + detector), backend/bank/templates.json
Outputs: ``unsafe_in_open_play()``: template ids to leave out of open-play moments

Workflow:
  - Sample open-play seconds (calm play, no restart pending) from every match.
  - Ask each template at that second, counting from the card opening, and settle it with the
    bank's own rule on the events of the next CARD_S seconds only.
  - P(settled within CARD_S) > UNSAFE_P → the template is unsafe in open play. At a dead ball
    (throw-in, free kick, goal kick, corner, stoppage) every template stays allowed: nobody
    touches the ball for 9–25 s.

CAUTION: data-driven by design; a new dataset changes the set. Measured on the replayed data,
so it uses the same matches the demo replays (a property of the data, not a peek at one moment).
"""

from __future__ import annotations

import bisect
from functools import lru_cache

from backend.agents import timing_rules as TR
from backend.agents.question_generator import event_dict
from backend.bank.context import ctx_for
from backend.bank.guards import window_for
from backend.bank.loader import load_bank
from backend.bank.types import MatchState, Player, Template
from backend.detect.detector import Detector
from backend.ingest import load_match
from backend.ingest.loader import list_match_ids
from backend.model.poisson import count_already
from backend.resolve.primitives import settle

CARD_S = TR.CARD_WINDOW_S  # how long a card stays up before it locks
UNSAFE_P = 0.5  # more likely than not to settle while the card is still up
SAMPLE_EVERY_S = 5  # one open-play sample every few seconds is plenty
DETECTOR_OPEN_PLAY = ("open_play", "stalled_near_box")  # the detector's names for the same
TYPICAL_PLAYER_WINDOW_S = 60


def _params(t: Template, possession: str | None, state: MatchState,
            player: Player | None) -> dict:
    """Params as the bank would ask them; a line is set one above what is already counted."""
    params: dict = {}
    for key in ("window", "window_s"):
        v = t.params.get(key)
        if isinstance(v, list):
            params[key] = v[0]
        elif v == "tuned":
            params[key] = TYPICAL_PLAYER_WINDOW_S
    if t.team_from != "none" and possession:
        params["team"] = possession
    if t.id == "player_stat_over" and player is not None:
        params["stat"] = t.resolve["stat_by_position"].get(player.position)
    if t.params.get("line") == "tuned" or t.resolve.get("n") == "line":
        params["line"] = count_already(t, params, state, player) + 1
    return params


def settle_share(match_id: str, bank: dict[str, Template]) -> dict[str, tuple[int, int]]:
    """Per template: (samples settled within CARD_S, samples) over one match's open play."""
    m = load_match(match_id)
    r = m.roster
    events = [event_dict(e) for e in m.events]
    keys = [(e.half, e.t) for e in m.events]
    players = [Player(p.player_id, p.name, p.team, p.position) for p in r.players if p.starter]
    roster = {p.player_id: p for p in players}
    counts = {tid: [0, 0] for tid in bank}
    n = 0
    for s in Detector(r, r.home, r.away).run(m):
        if (s.mode != "live" or s.situation not in DETECTOR_OPEN_PLAY or s.restart_pending
                or int(s.t) % SAMPLE_EVERY_S):
            continue
        upto = bisect.bisect_right(keys, (s.half, s.t))
        horizon = bisect.bisect_right(keys, (s.half, s.t + CARD_S))
        state = MatchState(m.match_id, s.t, s.half, r.home, r.away,
                           {r.home: r.home_name, r.away: r.away_name},
                           {r.home: s.score_home, r.away: s.score_away}, s.possession_team,
                           events[:upto], [], None, 0, roster)
        n += 1
        for tid, t in bank.items():
            player = None
            if t.kind == "player":
                fits = [p for p in players if not t.positions or p.position in t.positions]
                if not fits:
                    continue
                player = fits[n % len(fits)]
            params = _params(t, s.possession_team, state, player)
            window = window_for(t, params, state, s.t)  # count from the card opening
            got = settle(t.resolve, events[:horizon], window, ctx_for(t, params, state, player),
                         False, params)
            counts[tid][0] += got is not None
            counts[tid][1] += 1
    return {tid: (a, b) for tid, (a, b) in counts.items()}


@lru_cache(maxsize=1)
def open_play_settle_rates() -> dict[str, float]:
    """P(settled within CARD_S of the card opening | open play), pooled over every match."""
    bank = load_bank()
    total = {tid: [0, 0] for tid in bank}
    for mid in list_match_ids():
        for tid, (a, b) in settle_share(mid, bank).items():
            total[tid][0] += a
            total[tid][1] += b
    return {tid: a / b for tid, (a, b) in total.items() if b}


def unsafe_in_open_play() -> frozenset[str]:
    """Template ids to leave out of open-play moments."""
    return frozenset(t for t, p in open_play_settle_rates().items() if p > UNSAFE_P)
