"""question_generator_stub.py — placeholder generator filling moments from the catalog templates.

Last Modified: 2026-10-24 23:20, first version
Inputs:  DetectorState, Moment, favourite player id, Roster (docs/pipeline.md template catalog)
Outputs: backend.app.models.Question or None

Workflow:
  - Same interface as Stephen's generator; replaced automatically (see generator.get_propose).
  - Uses only the catalog's own question text. Choice is deterministic: situation first, then
    crc32(moment_id) over the templates that can be filled.
  - Lines and windows for player and goals questions are fixed placeholders, not model output.

CAUTION: this file invents no question content. Do not extend it with new templates.
"""

from __future__ import annotations

import logging
import re
import zlib

from pydantic import ValidationError

from backend.app.models import Option, Question
from backend.core.types import DetectorState, Moment, Roster, player_qid
from backend.model import top2

logger = logging.getLogger(__name__)

CATALOG_IDS = frozenset({
    "next_pass_dir", "keep_ball", "reach_final_third", "shot_soon", "goals_over", "team_wins",
    "card_second_half", "second_half_goal", "player_stat_over", "player_touch_window"})

YES_NO = (Option(id="yes", label="Yes"), Option(id="no", label="No"))
FORWARD_BACK = (Option(id="forward", label="Forward"), Option(id="backward", label="Back"))
PASS_LABELS = {"forward": "Forward", "sideways": "Sideways", "backward": "Back"}
PASS_ORDER = tuple(PASS_LABELS)  # display order of the two shown options
DEAD_ZONE_M = 5
MAX_TONE_CHARS = 40

# Catalog text and answer window in seconds (docs/pipeline.md).
INSTANT_TEXT = {
    "next_pass_dir": "Next pass: forward or back?",
    "keep_ball": "Will they keep the ball for 15s?",
    "reach_final_third": "Will they reach the final third?",
    "shot_soon": "Shot in the next 60s?",
}
INSTANT_WINDOW_S = {"next_pass_dir": 15, "keep_ball": 15, "reach_final_third": 20,
                    "shot_soon": 60}
# Possession templates only make sense while the ball is in ordinary play or a plain restart.
POSSESSION_SITUATIONS = frozenset({"calm_play", "stalled_near_box", "throw_in", "goal_kick"})
POSSESSION_TEMPLATES = ("keep_ball", "reach_final_third")
RESTART_SITUATIONS = frozenset({"throw_in", "goal_kick", "set_piece_wait"})

KICKOFF_FORECAST_IDS = ("goals_over", "team_wins")
HT_FORECAST_IDS = ("card_second_half", "second_half_goal")  # asked at half time only
FORECAST_TEXT = {
    "card_second_half": "Any card in the second half?",
    "second_half_goal": "Will there be a goal in the second half?",
}
MIN_GOALS_LINE = 3  # placeholder for the line the probability model will supply

# position -> (stat, line, touch window in seconds); placeholders for fair_line.
PLAYER_PLACEHOLDERS = {
    "GK": ("saves", 2, 180),
    "DF": ("clearances", 4, 60),
    "MF": ("completed passes", 20, 60),
    "FW": ("shots", 2, 90),
}


def _pick(moment_id: str, candidates: list[str]) -> str:
    """Deterministic choice among candidates."""
    return candidates[zlib.crc32(moment_id.encode("utf-8")) % len(candidates)]


def _base(moment: Moment, **kw) -> dict:
    tone = moment.tone[:MAX_TONE_CHARS] if moment.tone else None
    base = dict(qid=moment.moment_id, category=moment.category, open_at=moment.open_at,
                lock_at=moment.lock_at, hint_available=True, tone=tone,
                situation=moment.situation, lock_rule=moment.lock_rule)
    base.update(kw)
    return base


# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
#
# Instant, forecast and player fillers
#
# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%


def _pass_options(state: DetectorState) -> list[Option]:
    """The two likeliest pass directions (model top-2), shown in forward/sideways/back order."""
    try:
        shown, _ = top2("next_pass_dir", state, {"dead_zone_m": DEAD_ZONE_M})
    except (FileNotFoundError, ValueError) as exc:
        logger.warning("stub: model unavailable for next_pass_dir (%s); forward or back",
                       type(exc).__name__)
        return list(FORWARD_BACK)
    return [Option(id=o, label=PASS_LABELS[o]) for o in PASS_ORDER if o in shown]


def _instant(state: DetectorState, moment: Moment) -> Question:
    candidates = list(INSTANT_TEXT)
    if state.possession_team is None:
        candidates = ["shot_soon"]
    else:
        if state.ball_zone == "final_third":
            candidates.remove("reach_final_third")
        if moment.situation not in POSSESSION_SITUATIONS:
            candidates = [t for t in candidates if t not in POSSESSION_TEMPLATES]
    corner = (moment.situation == "set_piece_wait"
              and moment.situation_detail.get("restart") == "corner")
    if (moment.situation == "stalled_near_box" or corner) and "shot_soon" in candidates:
        tid = "shot_soon"  # corners go backward in the data, so never ask the pass question
    elif moment.situation in RESTART_SITUATIONS and "next_pass_dir" in candidates:
        tid = "next_pass_dir"
    else:
        tid = _pick(moment.moment_id, candidates)
    options = _pass_options(state) if tid == "next_pass_dir" else list(YES_NO)
    params = {"dead_zone_m": DEAD_ZONE_M} if tid == "next_pass_dir" else {}
    text = INSTANT_TEXT[tid]
    if tid == "next_pass_dir":  # the catalog wording, naming the two options actually shown
        text = f"Next pass: {options[0].label.lower()} or {options[1].label.lower()}?"
    return Question(**_base(
        moment, template_id=tid, text=text, options=options,
        resolve_by=moment.lock_at + INSTANT_WINDOW_S[tid], params=params))


def _forecast(state: DetectorState, moment: Moment, roster: Roster | None) -> Question:
    team = roster.home if roster else state.possession_team
    ids = KICKOFF_FORECAST_IDS + (HT_FORECAST_IDS if moment.kind == "ht_stack" else ())
    candidates = [t for t in ids if not (t == "team_wins" and team is None)]
    seq = re.search(r"(\d+)$", moment.moment_id)
    if moment.kind == "kickoff_stack" and seq and len(candidates) == len(KICKOFF_FORECAST_IDS):
        # consecutive ids in a stack: parity gives the two forecasts different templates
        tid = KICKOFF_FORECAST_IDS[int(seq.group(1)) % 2]
    else:
        tid = _pick(moment.moment_id, candidates)
    params: dict = {}
    if tid == "goals_over":
        line = max(MIN_GOALS_LINE, state.score_home + state.score_away + 1)
        params, text = {"line": line}, f"{line} or more goals in total?"
    elif tid == "team_wins":
        name = team
        if roster:
            name = roster.home_name if team == roster.home else roster.away_name
        params, text = {"team": team}, f"Will {name} win?"
    else:
        text = FORECAST_TEXT[tid]
    return Question(**_base(moment, template_id=tid, text=text, options=list(YES_NO),
                            resolve_by="FT", params=params))


def _player(moment: Moment, player_id: str | None, roster: Roster | None) -> Question | None:
    player = roster.by_id().get(player_id or "") if roster else None
    if player is None:
        return None
    if player.position not in PLAYER_PLACEHOLDERS:
        logger.warning("stub: moment %s: no placeholder for position %r",
                       moment.moment_id, player.position)
        return None
    stat, line, window_s = PLAYER_PLACEHOLDERS[player.position]
    if moment.kind == "player_instant":
        tid, params = "player_touch_window", {"window_s": window_s}
        text = f"Will {player.name} touch the ball in the next {window_s}s?"
        resolve_by: float | str = moment.lock_at + window_s
    else:
        tid, params = "player_stat_over", {"line": line, "stat": stat}
        text = f"Will {player.name} make {line}+ {stat} this half?"
        second_half = moment.kind == "ht_stack" or (moment.kind != "kickoff_stack"
                                                    and moment.half == 2)
        resolve_by = "FT" if second_half else "HT"
    return Question(**_base(
        moment, qid=player_qid(moment.moment_id, player.player_id), template_id=tid, text=text,
        options=list(YES_NO), resolve_by=resolve_by, params=params,
        player_id=player.player_id))


def propose(state: DetectorState, moment: Moment, user_player_id: str | None,
            roster: Roster | None = None) -> Question | None:
    """Fill one moment with a catalog template, or return None if it cannot be filled.

    Args:
        state: detector state when the moment is picked up.
        moment: the timing agent's order (category decides the family of template).
        user_player_id: the viewer's favourite player (player moments only).
        roster: names and positions; needed for player questions, optional otherwise.

    Returns:
        A two-option ``Question`` or None. Never raises: failures are logged with the moment id.
    """
    try:
        if moment.category == "player":
            return _player(moment, user_player_id, roster)
        if moment.category == "forecast":
            return _forecast(state, moment, roster)
        return _instant(state, moment)
    except (ValidationError, KeyError) as exc:
        logger.warning("stub: moment %s could not be filled (%s)", moment.moment_id,
                       type(exc).__name__)
        return None
