"""Build the resolver Ctx for a template, params, match state and player."""
from __future__ import annotations

from backend.bank.types import MatchState, Player, Template
from backend.resolve.primitives import Ctx


def ctx_for(template: Template, params: dict, state: MatchState,
            player: Player | None) -> Ctx:
    """Match context used by event filters, pricing and guards."""
    h, a = state.score.get(state.home, 0), state.score.get(state.away, 0)
    trailing = state.home if h < a else state.away if a < h else None
    gk_ids: dict[str, str] = {}
    for p in state.roster.values():
        if p.position == "GK" and p.team not in gk_ids:
            gk_ids[p.team] = p.player_id
    return Ctx(
        home=state.home, away=state.away, possession_team=state.possession_team,
        trailing_team=trailing,
        favourite=player.player_id if player else None,
        team=params.get("team"), gk_ids=gk_ids,
        favourite_position=player.position if player else None,
    )
