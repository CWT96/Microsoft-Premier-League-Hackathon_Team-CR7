"""Guardrails run before a template becomes a candidate (spec section 5)."""
from __future__ import annotations

from typing import Callable

from backend.bank.context import ctx_for
from backend.bank.types import MatchState, Player, Template, Window
from backend.resolve.primitives import settle

Guard = Callable[[Template, dict, MatchState, "Player | None", dict[str, float]], bool]

_MINUTES = {"5m": 300, "10m": 600, "20m": 1200}
HALF_LEN = 2700.0
_MATCH_LATEST = 4500.0
_HALF_MARGIN = 600.0
_SERVE_LAG = 6.0
_ACTIVE_S = 600.0


def _seconds(params: dict) -> float | None:
    """Window length in seconds for timed windows, else None."""
    w = params.get("window")
    if w in _MINUTES:
        return float(_MINUTES[w])
    if "window_s" in params:
        return float(params["window_s"])
    return None


def _kind(params: dict) -> str:
    """'timed', 'half' or 'match'. A template with no window means this half."""
    if _seconds(params) is not None:
        return "timed"
    return "match" if params.get("window") == "match" else "half"


def window_for(template: Template, params: dict, state: MatchState, lock_at: float) -> Window:
    """Resolver window for a candidate locked at lock_at."""
    if template.counts_from == "lock":
        start = lock_at
    elif template.counts_from == "half_start":
        start = 0.0 if state.half == 1 else HALF_LEN
    else:
        start = 0.0
    half = None if template.counts_from == "kickoff" else state.half
    # Kickoff counting starts at half 1, t=0; anything else starts in the state's half.
    start_half = 1 if template.counts_from == "kickoff" else state.half
    secs = _seconds(params)
    if secs is not None:
        return Window(start, lock_at + secs, None, half, start_half)
    if _kind(params) == "match":
        return Window(start, None, "full_end", None, start_half)
    return Window(start, None, "half_end", half, start_half)


def band_for(template: Template) -> tuple[float, float] | None:
    """Allowed range for the leading option's probability, or None for no band."""
    if template.v2_no_band:
        return None
    if template.rare:
        return (0.20, 0.80)
    if template.kind == "player":
        return (0.40, 0.60)
    return (0.15, 0.85)


def fits_clock(t: Template, params: dict, s: MatchState, player, probs) -> bool:
    """The window must end before the half ends (match: asked by 75:00)."""
    half_end = HALF_LEN * s.half
    kind = _kind(params)
    if kind == "timed":
        return s.t + _SERVE_LAG + (_seconds(params) or 0.0) <= half_end
    if kind == "half":
        return s.t <= half_end - _HALF_MARGIN
    return s.t <= _MATCH_LATEST


def not_decided(t: Template, params: dict, s: MatchState, player, probs) -> bool:
    """Skip if the answer is already settled by the events so far."""
    win = window_for(t, params, s, s.t)
    ctx = ctx_for(t, params, s, player)
    return settle(t.resolve, s.events, win, ctx, closed=False, params=params) is None


def prob_band(t: Template, params: dict, s: MatchState, player, probs) -> bool:
    """The leading option's probability must lie in the template's band."""
    band = band_for(t)
    if band is None:
        return True
    vals = sorted((probs.get(o, 0.0) for o in t.options), reverse=True)
    if len(vals) >= 3:
        vals = vals[:2]
    total = sum(vals)
    if total <= 0:
        return False
    return band[0] <= vals[0] / total <= band[1]


def no_repeat(t: Template, params: dict, s: MatchState, player, probs) -> bool:
    """Not currently open and not the last one asked."""
    return t.id not in s.open_template_ids and t.id != s.last_template_id


def max_open_windows(t: Template, params: dict, s: MatchState, player, probs) -> bool:
    """At most 2 window questions waiting to settle."""
    return s.open_window_count < 2


def sent_off(player: Player, events: list[dict]) -> bool:
    """True if the player has been shown a red card."""
    return any(e.get("player_id") == player.player_id and e.get("type") == "card"
               and e.get("outcome") == "red" for e in events)


def player_active(t: Template, params: dict, s: MatchState, player, probs) -> bool:
    """Player acted in the last 10 minutes and has not been sent off."""
    if player is None or sent_off(player, s.events):
        return False
    return any(e["t"] >= s.t - _ACTIVE_S for e in s.events
               if e.get("player_id") == player.player_id)


def position_fit(t: Template, params: dict, s: MatchState, player, probs) -> bool:
    """The player's position is one the template applies to."""
    if player is None:
        return False
    return t.positions is None or player.position in t.positions


def needs_lead_of_one(t: Template, params: dict, s: MatchState, player, probs) -> bool:
    """One team leads by exactly one goal and params["team"] is the trailing team."""
    h, a = s.score.get(s.home, 0), s.score.get(s.away, 0)
    if abs(h - a) != 1:
        return False
    trailing = s.home if h < a else s.away
    team = params.get("team")
    return team is None or team == trailing


def p_none_below_035(t: Template, params: dict, s: MatchState, player, probs) -> bool:
    """Chance of no more goals is under 0.35, so the question rarely voids."""
    return probs.get("none", 0.0) < 0.35


GUARDS: dict[str, Guard] = {
    "fits_clock": fits_clock,
    "not_decided": not_decided,
    "prob_band": prob_band,
    "no_repeat": no_repeat,
    "max_open_windows": max_open_windows,
    "player_active": player_active,
    "position_fit": position_fit,
    "needs_lead_of_one": needs_lead_of_one,
    "p_none_below_0.35": p_none_below_035,
}
