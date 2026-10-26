"""A minimal ProposeFn for app tests that do not exercise the bank: yes/no, shared qid."""

from backend.app.models import Option, Question
from backend.core.types import player_qid


def fake_propose(state, moment, user_player_id, roster=None) -> Question | None:
    """One yes/no question per moment (per user for player moments)."""
    player = moment.category == "player"
    if player and user_player_id is None:
        return None
    return Question(
        qid=player_qid(moment.moment_id, user_player_id) if player else moment.moment_id,
        category=moment.category, template_id="goal_window", text="Goal soon?",
        options=[Option(id="yes", label="Yes"), Option(id="no", label="No")],
        open_at=moment.open_at, lock_at=moment.lock_at, resolve_by="FT", hint_available=True,
        player_id=user_player_id if player else None, situation=moment.situation,
        lock_rule=moment.lock_rule)
