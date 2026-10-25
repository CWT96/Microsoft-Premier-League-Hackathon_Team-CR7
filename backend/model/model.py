"""model.py — frequency tables fitted on one match, and the cached ``get_model``.

Last Modified: 2026-10-24 23:00, first version.
Inputs:  a ``Match`` (events + roster); ``get_model`` loads it through ``backend.ingest``
Outputs: ``Model`` (immutable by convention: do not mutate its dicts)

Workflow:
  - Every possession event is a sample "the ball is here now". The outcome of each instant
    template is labelled by calling the resolver's own rule (``backend.resolve.rules``), so the
    model estimates exactly what the resolver calls "yes".
  - Context keys run coarse to fine: () -> (zone,) -> (zone, passes bucket or threat flag).
  - Player tables hold stat counts, touches and time on the pitch for the Gamma-Poisson rates.
  - Match-level priors (goals, cards) are counted here and shrunk in ``probs.py``.

CAUTION: the model is fitted on the whole synthetic match it is later asked about.
Training "threat" is a crude proxy (an own-team event at x >= HIGH_THREAT_X in the
last 10 s) because xT needs the detector. Tracking (pressure) is not used.
"""

from __future__ import annotations

import threading
from collections import defaultdict
from dataclasses import dataclass
from typing import Iterator

from backend.app.models import Option, Question
from backend.core.constants import (DEFAULT_MATCH_ID, FINAL_THIRD_MIN_X, HALF_LENGTH_S,
                                    OWN_THIRD_MAX_X,
                                    PITCH_LENGTH_M, SECOND_HALF_START_S)
from backend.core.types import BallZone, Match, MatchEvent
from backend.resolve import rules as R
from backend.resolve.registry import RULES, RuleContext, split_after

# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
#
# Constants
#
# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

SAMPLE_TYPES = (R.POSSESSION_TYPES - {"kickoff"}) | R.RESTART_TYPES
PASS_BUCKET_EDGES = (3, 6)  # passes in the possession: 0-2 short, 3-5 mid, 6+ long
HIGH_THREAT_X = 88.0  # training proxy for "threat >= THREAT_HIGH"
THREAT_LOOKBACK_S = 10.0
PASS_DIRECTIONS = ("forward", "sideways", "backward")
YES_NO = ("yes", "no")

Context = tuple  # (), (zone,), (zone, bucket)
Counts = dict[Context, dict[str, float]]


# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
#
# Types
#
# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%


@dataclass(frozen=True)
class Sample:
    """One training point: the ball state after an event, and what happened next.

    An outcome of ``None`` means the resolver would void the question.
    """

    t: float
    half: int
    zone: BallZone
    passes: int
    high_threat: bool
    next_dir: str | None
    keep: bool | None
    final3: bool | None
    shot: bool | None


@dataclass(frozen=True)
class PlayerStats:
    position: str
    seconds_on: float
    touches: int
    stat_count: int


@dataclass(frozen=True)
class Model:
    match_id: str
    home: str
    away: str
    total_seconds: float
    tables: dict[str, Counts]  # template_id -> context -> outcome -> count
    players: dict[str, PlayerStats]
    goals: dict[str, int]  # team -> goals
    cards_second_half: int
    goals_second_half: int
    stat_rate: dict[str, float]  # player -> shrunk stat count per half on the pitch
    touch_rate: dict[str, float]  # player -> shrunk touches per second on the pitch


# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
#
# Features
#
# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%


def zone_of(x: float) -> BallZone:
    """Third of the pitch for x in the possession team's attacking frame."""
    if x < OWN_THIRD_MAX_X:
        return "own_third"
    return "final_third" if x > FINAL_THIRD_MIN_X else "middle_third"


def pass_bucket(passes: int) -> str:
    """Bucket of passes in the current possession."""
    low, high = PASS_BUCKET_EDGES
    return "short" if passes < low else "mid" if passes < high else "long"


def pitch_seconds(match: Match, half: int, t: float) -> float:
    """Seconds actually played up to displayed clock ``t`` (half 2 starts after stoppage)."""
    return t if half == 1 else match.first_half_end_t + (t - SECOND_HALF_START_S)


# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
#
# Samples
#
# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%


def _question(template_id: str, team: str) -> Question:
    return Question(qid="fit", category="instant", template_id=template_id, text="?",
                    options=[Option(id="yes", label="Yes"), Option(id="no", label="No")],
                    open_at=0, lock_at=0, resolve_by=0.0, hint_available=False,
                    params={"team": team})


def _next_direction(ctx: RuleContext) -> str | None:
    """Direction of the first pass after the lock, by the resolver's dead zone and stops."""
    for e in R._window(ctx, R.NEXT_PASS_WINDOW_S):
        if e.type in R.PASS_STOP_TYPES:
            return None
        if e.type == "pass" and e.start and e.end:
            dx = e.end[0] - e.start[0]
            return ("forward" if dx > R.DEAD_ZONE_M else "backward" if dx < -R.DEAD_ZONE_M
                    else "sideways")
    return None


def _yes_no(template_id: str, ctx: RuleContext, team: str) -> bool | None:
    verdict = RULES[template_id](_question(template_id, team), ctx)
    return None if verdict is None or verdict.option is None else verdict.option == "yes"


def _holder_x(ctx: RuleContext, e: MatchEvent, holder: str) -> float:
    """Ball x after the event, in the holder's attacking frame."""
    pts = R._x_points(e) or ([e.start[0]] if e.start else [PITCH_LENGTH_M / 2])
    return pts[-1] if holder == e.team else PITCH_LENGTH_M - pts[-1]


def iter_instant_samples(match: Match) -> Iterator[Sample]:
    """Yield one ``Sample`` per possession event, labelled with the resolver's own rules."""
    events = list(match.events)
    for i, e in enumerate(events):
        if e.type not in SAMPLE_TYPES:
            continue
        lock = (e.half, e.t)
        split = split_after(events, lock)

        def ctx_at(window: float, e: MatchEvent = e, lock: tuple = lock, split: int = split
                   ) -> RuleContext:
            return RuleContext(match.roster, events, split, lock, (e.half, e.t + window))

        holder = R._holder(ctx_at(0), e)
        zone = zone_of(_holder_x(ctx_at(0), e, holder))
        passes = 0
        for prev in reversed(events[: i + 1]):
            if prev.type == "pass" and prev.team == holder:
                passes += 1
            elif not (prev.type == "carry" and prev.team == holder):
                break
        high = any(p.half == e.half and e.t - p.t <= THREAT_LOOKBACK_S and p.team == holder
                   and any(x >= HIGH_THREAT_X for x in R._x_points(p)) for p in events[: i + 1])
        yield Sample(
            t=e.t, half=e.half, zone=zone, passes=passes, high_threat=high,
            next_dir=_next_direction(ctx_at(R.NEXT_PASS_WINDOW_S)),
            keep=_yes_no("keep_ball", ctx_at(R.KEEP_BALL_WINDOW_S), holder),
            final3=_yes_no("reach_final_third", ctx_at(R.FINAL_THIRD_WINDOW_S), holder),
            shot=_yes_no("shot_soon", ctx_at(R.SHOT_SOON_WINDOW_S), holder),
        )


STAT_PSEUDO_HALVES = 1.0
TOUCH_PSEUDO_SECONDS = 600.0


def _player_rates(players: dict[str, PlayerStats], total_seconds: float
                  ) -> tuple[dict[str, float], dict[str, float]]:
    """Gamma-Poisson rates per player, shrunk towards the position (works for 0 s on pitch)."""
    stat_rate: dict[str, float] = {}
    touch_rate: dict[str, float] = {}
    for pid, pl in players.items():
        mates = [q for q in players.values() if q.position == pl.position]
        mate_s = sum(q.seconds_on for q in mates)
        stat_prior = sum(q.stat_count for q in mates) / (mate_s / HALF_LENGTH_S) if mate_s else 0.0
        touch_prior = sum(q.touches for q in mates) / mate_s if mate_s else 0.0
        own_halves = pl.seconds_on / total_seconds * 2.0
        stat_rate[pid] = ((pl.stat_count + STAT_PSEUDO_HALVES * stat_prior)
                          / (own_halves + STAT_PSEUDO_HALVES))
        touch_rate[pid] = ((pl.touches + TOUCH_PSEUDO_SECONDS * touch_prior)
                           / (pl.seconds_on + TOUCH_PSEUDO_SECONDS))
    return stat_rate, touch_rate


# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
#
# Fit
#
# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%


def _count(table: Counts, contexts: tuple[Context, ...], outcome: str) -> None:
    for c in contexts:
        table.setdefault(c, defaultdict(float))[outcome] += 1.0


def _seconds_on(match: Match) -> dict[str, float]:
    """Seconds each rostered player was on the pitch, from starters and substitutions."""
    total = pitch_seconds(match, 2, match.full_end_t)
    sec = {p.player_id: (total if p.starter else 0.0) for p in match.roster.players}
    for e in match.events:
        if e.type == "substitution" and e.player_id and e.related_player_id:
            at = pitch_seconds(match, e.half, e.t)
            sec[e.player_id] = at
            sec[e.related_player_id] = total - at
    return sec


def fit(match: Match) -> Model:
    """Fit all frequency tables from one match. Deterministic.

    Args:
        match: the loaded match (events sorted by ``(half, t, id)``).

    Returns:
        The fitted ``Model``.
    """
    tables: dict[str, Counts] = {k: {} for k in ("next_pass_dir", "keep_ball",
                                                 "reach_final_third", "shot_soon")}
    for s in iter_instant_samples(match):
        ctx_pass = ((), (s.zone,), (s.zone, pass_bucket(s.passes)))
        ctx_shot = ((), (s.zone,), (s.zone, s.high_threat))
        if s.next_dir is not None:
            _count(tables["next_pass_dir"], ctx_pass, s.next_dir)
        for tid, flag, ctxs in (("keep_ball", s.keep, ctx_pass),
                                ("reach_final_third", s.final3, ctx_pass[:2]),
                                ("shot_soon", s.shot, ctx_shot)):
            if flag is not None:
                _count(tables[tid], ctxs, "yes" if flag else "no")

    seconds = _seconds_on(match)
    by_id = match.roster.by_id()
    touches: dict[str, int] = defaultdict(int)
    stats: dict[str, int] = defaultdict(int)
    goals: dict[str, int] = defaultdict(int)
    cards_2h = goals_2h = 0
    for e in match.events:
        if e.player_id in by_id and e.type not in R.NOT_A_TOUCH_TYPES:
            touches[e.player_id] += 1
            if R.STAT_BY_POSITION[by_id[e.player_id].position][1](e):
                stats[e.player_id] += 1
        if e.type == "goal":
            goals[e.team] += 1
            goals_2h += e.half == 2
        if e.type == "card":
            cards_2h += e.half == 2
    players = {p.player_id: PlayerStats(p.position, seconds[p.player_id], touches[p.player_id],
                                        stats[p.player_id]) for p in match.roster.players}
    total = pitch_seconds(match, 2, match.full_end_t)
    stat_rate, touch_rate = _player_rates(players, total)
    return Model(match.match_id, match.roster.home, match.roster.away, total, tables, players,
                 dict(goals), cards_2h, goals_2h, stat_rate, touch_rate)


# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
#
# Cache
#
# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

_CACHE: dict[str, Model] = {}
_LOCK = threading.Lock()


def cache_model(model: Model) -> None:
    """Store a fitted model so ``get_model(model.match_id)`` returns it."""
    with _LOCK:
        _CACHE[model.match_id] = model


def clear_cache() -> None:
    """Forget every cached model (tests, reload)."""
    with _LOCK:
        _CACHE.clear()


def get_model(match_id: str = DEFAULT_MATCH_ID) -> Model:
    """The model for a match, fitted on first use and cached (thread-safe).

    The app should call this once at startup so no request pays for the fit.

    Raises:
        FileNotFoundError: if the match cannot be loaded.
    """
    model = _CACHE.get(match_id)
    if model is None:
        with _LOCK:
            model = _CACHE.get(match_id)  # double-checked: another thread may have fitted
            if model is None:
                from backend.ingest import load_match  # lazy: ingest lands in another branch

                model = _CACHE[match_id] = fit(load_match(match_id, with_tracking=False))
    return model
