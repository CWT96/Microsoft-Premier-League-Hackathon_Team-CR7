"""Build every valid candidate question of one kind at this moment."""
from __future__ import annotations

from backend.bank.context import ctx_for
from backend.bank.guards import GUARDS, HALF_LEN, _seconds, window_for
from backend.bank.loader import fill_text
from backend.bank.types import CATEGORY, Candidate, Kind, MatchState, Player, Template
from backend.model.poisson import PRICERS, count_already, tune_player

_SERVE_LAG = 6.0
_FULL_S = 5400.0


def _teams(t: Template, state: MatchState) -> list[str | None] | None:
    """Teams to expand over ([None] = no team param), or None to skip the template."""
    if t.team_from == "none":
        return [None]
    if t.team_from == "each":
        return [state.home, state.away]
    if t.team_from == "possession":
        team = state.possession_team
    else:
        h, a = state.score.get(state.home, 0), state.score.get(state.away, 0)
        team = state.home if h < a else state.away if a < h else None
    return None if team is None else [team]


def _window_values(t: Template) -> list:
    """Allowed values of the window param (window or window_s), or [None]."""
    for key in ("window", "window_s"):
        v = t.params.get(key)
        if isinstance(v, list):
            return [(key, x) for x in v]
        if v == "tuned":
            return [(key, "tuned")]
    return [(None, None)]


def _length(params: dict, state: MatchState) -> float:
    """Rough window length used to order rare-template windows (shortest first)."""
    secs = _seconds(params)
    if secs is not None:
        return secs
    if params.get("window") == "match":
        return _FULL_S - state.t
    return HALF_LEN * state.half - state.t


def _resolve_by(params: dict, state: MatchState, lock_at: float):
    secs = _seconds(params)
    if secs is not None:
        return lock_at + secs
    if params.get("window") == "match":
        return "FT"
    return "HT" if state.half == 1 else "FT"


def _tune_line(t: Template, params: dict, state: MatchState, player) -> int:
    """Line 1..8 whose P(yes) is closest to 0.5."""
    best, best_d = 1, 2.0
    for line in range(1, 9):
        p = PRICERS[t.prob](t, {**params, "line": line}, state, player)["yes"]
        if abs(p - 0.5) < best_d:
            best, best_d = line, abs(p - 0.5)
    return best


def _options(t: Template, probs: dict[str, float]) -> tuple[list[str], dict[str, float]]:
    """Shown options and renormalised probabilities (top 2 of 3)."""
    opts = list(t.options)
    if len(opts) >= 3:
        opts = sorted(opts, key=lambda o: probs.get(o, 0.0), reverse=True)[:2]
    total = sum(probs.get(o, 0.0) for o in opts)
    p = {o: (probs.get(o, 0.0) / total if total > 0 else 1.0 / len(opts)) for o in opts}
    return opts, p


def _try(t: Template, params: dict, state: MatchState, player, lock_at: float) -> Candidate | None:
    probs = PRICERS[t.prob](t, params, state, player)
    for name in t.guards:
        if not GUARDS[name](t, params, state, player, probs):
            return None
    opts, p = _options(t, probs)
    return Candidate(
        template_id=t.id, kind=t.kind, category=CATEGORY[t.kind], params=params,
        text=fill_text(t, params, state, player), options=opts, p=p,
        window=window_for(t, params, state, lock_at),
        resolve_by=_resolve_by(params, state, lock_at),
        player_id=player.player_id if player else None,
        context=ctx_for(t, params, state, player),
    )


def build_candidates(kind: Kind, state: MatchState, bank: dict[str, Template],
                     open_at: float, player: Player | None = None) -> list[Candidate]:
    """All valid candidates of one kind at this moment."""
    if kind == "player" and player is None:
        return []
    lock_at = open_at + _SERVE_LAG
    half_end = HALF_LEN * state.half
    minutes_left = (half_end - state.t) / 60
    # A tuned window starts at the lock, so it must fit between lock_at and half end.
    window_minutes_left = (half_end - lock_at) / 60
    out: list[Candidate] = []
    for t in bank.values():
        if t.kind != kind:
            continue
        pl = player if kind == "player" else None
        if kind == "player" and not GUARDS["position_fit"](t, {}, state, pl, {}):
            continue
        teams = _teams(t, state)
        if teams is None:
            continue
        stat = None
        if kind == "player":
            stat = (t.resolve["filter"].get("stat")
                    or t.resolve.get("stat_by_position", {}).get(pl.position))
            needs_stat = "stat_by_position" in t.resolve or "stat" in t.resolve["filter"]
            if needs_stat and stat is None:
                continue
        found: list[Candidate] = []
        for team in teams:
            for key, val in _window_values(t):
                params: dict = {}
                if key:
                    params[key] = val
                if team is not None:
                    params["team"] = team
                if t.id == "player_stat_over":
                    params["stat"] = stat
                if key == "window_s" and val == "tuned":
                    params["window_s"] = tune_player(pl.position, stat, "window_s",
                                                    window_minutes_left)[0]
                if t.params.get("line") == "tuned":
                    if kind == "player":
                        # The line counts the whole half, so add what is already made.
                        tuned = tune_player(pl.position, stat, "line", minutes_left)[0]
                        params["line"] = count_already(t, params, state, pl) + tuned
                    else:
                        params["line"] = _tune_line(t, params, state, pl)
                c = _try(t, params, state, pl, lock_at)
                if c is not None:
                    found.append(c)
        if t.rare and found:
            found = [min(found, key=lambda c: _length(c.params, state))]
        out.extend(found)
    return out
