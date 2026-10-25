"""probs.py — probability functions for the 10 catalog templates and the public model API.

Last Modified: 2026-10-24 23:00, first version.
Inputs:  a fitted ``Model`` (default: ``get_model()``), a ``DetectorState``, question params
Outputs: option probabilities, top-2 options, AI hint, fair lines

Workflow:
  - Table templates back off coarse to fine with Dirichlet smoothing (``TABLE_PSEUDO_COUNT``).
  - Forecast templates use Poisson goal/card rates shrunk towards football priors.
  - Player templates use Gamma-Poisson rates shrunk towards the player's position.
  - ``fair_line`` picks the line or window whose P(yes) is closest to 0.5, with a [0.4, 0.6]
    guardrail that falls back to ``player_touch_window`` (always tunable).
  - Player params carry ``player_id`` next to ``line``/``window_s``. The caller copies it from
    ``Question.player_id`` (the resolver reads that field, not ``params``).
  - ``dull_parts["possession"]`` is min(passes / 6, 1), so 6 or more passes share one bucket.
  - The model is fitted on the whole synthetic match it is later asked about.

CAUTION: probabilities are clamped to [P_MIN, 1 - P_MIN]. Tracking (pressure) is not used.
"""

from __future__ import annotations

import logging
import math
from typing import Any, Mapping

from backend.core.constants import HALF_LENGTH_S, NOMINAL_HALF_END_S
from backend.core.geometry import zone_of
from backend.core.types import BallZone, DetectorState
from backend.model.model import (PASS_DIRECTIONS, YES_NO, Context, Counts, Model, get_model,
                                 pass_bucket)
from backend.model.registry import get_prob, register_prob
from backend.resolve.rules import STAT_BY_POSITION

LOG = logging.getLogger(__name__)

# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
#
# Constants
#
# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

P_MIN = 0.01
TABLE_PSEUDO_COUNT = 4.0
DEFAULT_ZONE: BallZone = "middle_third"
THREAT_HIGH = 0.15  # state.threat at or above this is "high"
POSSESSION_PASS_CAP = 6  # dull_parts["possession"] = min(passes / 6, 1)
MATCH_S = 2 * HALF_LENGTH_S

# Match-level priors (football averages) and how many matches of weight they carry.
PRIOR_MATCH_WEIGHT = 2.0
PRIOR_GOALS_PER_TEAM = 1.35
PRIOR_CARDS_SECOND_HALF = 1.2
PRIOR_GOALS_SECOND_HALF = 1.5
POISSON_MAX = 15

LINE_MAX = 60
WINDOW_GRID_S = tuple(range(30, 301, 5))
TARGET_P = 0.5
FAIR_LOW, FAIR_HIGH = 0.4, 0.6
MIN_HALF_FRACTION = 0.05


# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
#
# Helpers
#
# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%


def _clamp(p: float) -> float:
    return min(max(p, P_MIN), 1.0 - P_MIN)


def _binary(p_yes: float) -> dict[str, float]:
    p = _clamp(p_yes)
    return {"yes": p, "no": 1.0 - p}


def _smoothed(table: Counts, outcomes: tuple[str, ...], levels: list[Context]
              ) -> dict[str, float]:
    """Back-off estimate: uniform -> coarse context -> fine context, each Dirichlet-smoothed."""
    dist = {o: 1.0 / len(outcomes) for o in outcomes}
    for ctx in levels:
        counts = table.get(ctx, {})
        n = sum(counts.get(o, 0.0) for o in outcomes)
        dist = {o: (counts.get(o, 0.0) + TABLE_PSEUDO_COUNT * dist[o]) / (n + TABLE_PSEUDO_COUNT)
                for o in outcomes}
    return dist


def _zone(state: DetectorState) -> BallZone:
    if state.ball_zone is not None:
        return state.ball_zone
    return zone_of(state.ball_xy[0]) if state.ball_xy is not None else DEFAULT_ZONE


def _pass_levels(state: DetectorState) -> list[Context]:
    zone = _zone(state)
    levels: list[Context] = [(), (zone,)]
    share = state.dull_parts.get("possession")
    if share is not None:
        levels.append((zone, pass_bucket(round(share * POSSESSION_PASS_CAP))))
    return levels


def _poisson_sf(k: int, lam: float) -> float:
    """P(X >= k) for X ~ Poisson(lam)."""
    if k <= 0:
        return 1.0
    return max(0.0, 1.0 - sum(math.exp(-lam) * lam**i / math.factorial(i) for i in range(k)))


def _poisson_pmf(lam: float) -> list[float]:
    return [math.exp(-lam) * lam**i / math.factorial(i) for i in range(POISSON_MAX + 1)]


def _frac_left(state: DetectorState) -> float:
    return min(max((MATCH_S - state.t) / MATCH_S, 0.0), 1.0)


def _second_half_frac(state: DetectorState) -> float:
    """Share of the second half still to play (1 until it starts)."""
    return min(max((NOMINAL_HALF_END_S[2] - state.t) / HALF_LENGTH_S, 0.0), 1.0)


def _shrunk(observed: float, prior: float) -> float:
    """Per-match rate from one observed match and a prior worth ``PRIOR_MATCH_WEIGHT`` matches."""
    return (observed + PRIOR_MATCH_WEIGHT * prior) / (1.0 + PRIOR_MATCH_WEIGHT)


# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
#
# Instant templates
#
# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%


def _next_pass_dir(model: Model, state: DetectorState, params: dict[str, Any]
                   ) -> dict[str, float]:
    dist = _smoothed(model.tables["next_pass_dir"], PASS_DIRECTIONS, _pass_levels(state))
    clamped = {o: _clamp(p) for o, p in dist.items()}
    total = sum(clamped.values())
    return {o: p / total for o, p in clamped.items()}


def _keep_ball(model: Model, state: DetectorState, params: dict[str, Any]) -> dict[str, float]:
    return _binary(_smoothed(model.tables["keep_ball"], YES_NO, _pass_levels(state))["yes"])


def _reach_final_third(model: Model, state: DetectorState, params: dict[str, Any]
                       ) -> dict[str, float]:
    levels = _pass_levels(state)[:2]
    return _binary(_smoothed(model.tables["reach_final_third"], YES_NO, levels)["yes"])


def _shot_soon(model: Model, state: DetectorState, params: dict[str, Any]) -> dict[str, float]:
    zone = _zone(state)
    levels: list[Context] = [(), (zone,), (zone, state.threat >= THREAT_HIGH)]
    return _binary(_smoothed(model.tables["shot_soon"], YES_NO, levels)["yes"])


# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
#
# Forecast templates
#
# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%


def _team_goal_rate(model: Model, team: str) -> float:
    return _shrunk(model.goals.get(team, 0), PRIOR_GOALS_PER_TEAM)


def _goals_over(model: Model, state: DetectorState, params: dict[str, Any]
                ) -> dict[str, float]:
    rate = (_team_goal_rate(model, model.home) + _team_goal_rate(model, model.away))
    need = math.ceil(params["line"]) - (state.score_home + state.score_away)
    return _binary(_poisson_sf(need, rate * _frac_left(state)))


def _team_wins(model: Model, state: DetectorState, params: dict[str, Any]) -> dict[str, float]:
    team = params["team"]
    if team not in (model.home, model.away):
        raise ValueError(f"unknown team {team!r}")
    other = model.away if team == model.home else model.home
    lead = (state.score_home - state.score_away) * (1 if team == model.home else -1)
    frac = _frac_left(state)
    pf = _poisson_pmf(_team_goal_rate(model, team) * frac)
    pa = _poisson_pmf(_team_goal_rate(model, other) * frac)
    win = sum(pf[i] * pa[j] for i in range(POISSON_MAX + 1) for j in range(POISSON_MAX + 1)
              if lead + i - j > 0)
    return _binary(win)


def _card_second_half(model: Model, state: DetectorState, params: dict[str, Any]
                      ) -> dict[str, float]:
    rate = _shrunk(model.cards_second_half, PRIOR_CARDS_SECOND_HALF)
    return _binary(1.0 - math.exp(-rate * _second_half_frac(state)))


def _second_half_goal(model: Model, state: DetectorState, params: dict[str, Any]
                      ) -> dict[str, float]:
    rate = _shrunk(model.goals_second_half, PRIOR_GOALS_SECOND_HALF)
    return _binary(1.0 - math.exp(-rate * _second_half_frac(state)))


# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
#
# Player templates
#
# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%


def _player(model: Model, player_id: str):
    try:
        return model.players[player_id]
    except KeyError:
        raise ValueError(f"unknown player {player_id!r}") from None


def _half_fraction(state: DetectorState) -> float:
    """Share of the asked half still to play. Lobby, breaks and half time mean a whole half."""
    if state.mode in ("lobby", "break", "full_time") or state.half not in NOMINAL_HALF_END_S:
        return 1.0
    end = NOMINAL_HALF_END_S[state.half]
    return min(max((end - state.t) / HALF_LENGTH_S, MIN_HALF_FRACTION), 1.0)


def _stat_rate_per_half(model: Model, player_id: str) -> float:
    _player(model, player_id)
    return model.stat_rate[player_id]


def _touch_rate_per_s(model: Model, player_id: str) -> float:
    _player(model, player_id)
    return model.touch_rate[player_id]


def _stat_p(model: Model, state: DetectorState, player_id: str, line: int) -> float:
    lam = _stat_rate_per_half(model, player_id) * _half_fraction(state)
    return _clamp(_poisson_sf(math.ceil(line), lam))


def _touch_p(model: Model, player_id: str, window_s: float) -> float:
    return _clamp(1.0 - math.exp(-_touch_rate_per_s(model, player_id) * window_s))


def _player_stat_over(model: Model, state: DetectorState, params: dict[str, Any]
                      ) -> dict[str, float]:
    return _binary(_stat_p(model, state, params["player_id"], params["line"]))


def _player_touch_window(model: Model, state: DetectorState, params: dict[str, Any]
                         ) -> dict[str, float]:
    return _binary(_touch_p(model, params["player_id"], params["window_s"]))


for _id, _fn in (
    ("next_pass_dir", _next_pass_dir), ("keep_ball", _keep_ball),
    ("reach_final_third", _reach_final_third), ("shot_soon", _shot_soon),
    ("goals_over", _goals_over), ("team_wins", _team_wins),
    ("card_second_half", _card_second_half), ("second_half_goal", _second_half_goal),
    ("player_stat_over", _player_stat_over), ("player_touch_window", _player_touch_window),
):
    register_prob(_id, _fn)


# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
#
# Public API
#
# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%


def option_probs(template_id: str, state: DetectorState, params: dict[str, Any] | None = None,
                 *, model: Model | None = None) -> dict[str, float]:
    """Probability of every outcome of a template (sums to 1, each inside (0, 1)).

    Args:
        template_id: catalog id.
        state: detector state at the moment the question is asked.
        params: question params. Player templates also need ``player_id``.
        model: fitted model; defaults to ``get_model()`` for the default match.

    Raises:
        ValueError: unknown template, player or team.
    """
    fn = get_prob(template_id)
    return fn(model or get_model(), state, dict(params or {}))


def top2(template_id: str, state: DetectorState, params: dict[str, Any] | None = None,
         *, model: Model | None = None) -> tuple[tuple[str, str], tuple[float, float]]:
    """The two most likely outcomes (most likely first) and their renormalised probabilities."""
    probs = option_probs(template_id, state, params, model=model)
    ranked = sorted(probs, key=lambda o: (-probs[o], o))[:2]
    total = sum(probs[o] for o in ranked)
    return (ranked[0], ranked[1]), (probs[ranked[0]] / total, probs[ranked[1]] / total)


def hint(probs_shown: Mapping[str, float]) -> tuple[str, float]:
    """The AI pick among the two shown options, with its probability renormalised over them.

    Raises:
        ValueError: if the mapping does not hold exactly two options.
    """
    if len(probs_shown) != 2:
        raise ValueError("hint needs exactly the two shown options")
    total = sum(probs_shown.values())
    pick = min(probs_shown, key=lambda o: (-probs_shown[o], o))  # same tie-break as top2
    return pick, probs_shown[pick] / total


def fair_line(template_id: str, player_id: str, state: DetectorState,
              *, model: Model | None = None) -> dict[str, Any]:
    """Tune a player question so P(yes) is closest to 0.5.

    ``player_stat_over`` picks the integer ``line``. If its P(yes) falls outside [0.4, 0.6]
    (or ``template_id`` is ``player_touch_window``), ``window_s`` is tuned instead.

    Returns:
        ``{"template_id", "params", "p", "in_range"}``; stat questions also carry ``"stat"``.
        ``in_range`` is False when even the touch window cannot reach [0.4, 0.6]: the generator
        should then skip the player.

    Raises:
        ValueError: unknown player or template.
    """
    m = model or get_model()
    pl = _player(m, player_id)
    if template_id == "player_stat_over":
        line = min(range(1, LINE_MAX + 1),
                   key=lambda k: (abs(_stat_p(m, state, player_id, k) - TARGET_P), k))
        p = _stat_p(m, state, player_id, line)
        if FAIR_LOW <= p <= FAIR_HIGH:
            return {"template_id": template_id, "params": {"line": line}, "p": p,
                    "stat": STAT_BY_POSITION[pl.position][0], "in_range": True}
    elif template_id != "player_touch_window":
        raise ValueError(f"no fair line for template {template_id!r}")
    window = min(WINDOW_GRID_S, key=lambda w: (abs(_touch_p(m, player_id, w) - TARGET_P), w))
    p = _touch_p(m, player_id, window)
    in_range = FAIR_LOW <= p <= FAIR_HIGH
    if not in_range:
        LOG.warning("fair_line: %s has no window with P in [%.1f, %.1f] (best %.3f at %d s)",
                    player_id, FAIR_LOW, FAIR_HIGH, p, window)
    return {"template_id": "player_touch_window", "params": {"window_s": window}, "p": p,
            "in_range": in_range}
