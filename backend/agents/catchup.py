"""catchup.py — build the catch-up card shown after an attack ends.

Last Modified: 2026-10-24 22:55, first version (code picks events/facts, LLM or template words)
Inputs:  the attack's MatchEvents, Roster; backend/agents/prompts/catchup.prompt
Outputs: dict for the ``catchup.card`` message (contracts/README.md)

Workflow:
  - Code picks 3 to 5 key events: the final event, the last shot or goal, and the passes that
    gained the most ground.
  - Code computes the facts (shot distance to goal centre, passes, duration, names).
  - The model (agent "catchup") or a template phrases headline and explanation; every number
    must appear in the facts, otherwise the template is used.

CAUTION: coordinates are in the acting team's frame (attack left to right), so the goal centre
         is (105, 34) for the attacking team's shots.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Sequence
from typing import Any

from backend.agents import llm
from backend.core.constants import GOAL_CENTRE
from backend.core.types import MatchEvent, Roster

logger = logging.getLogger(__name__)

AGENT = "catchup"
MIN_KEY_EVENTS = 3
MAX_KEY_EVENTS = 5
MAX_HEADLINE_CHARS = 80
MAX_EXPLANATION_CHARS = 240
WON_BALL_TYPES = frozenset({"tackle", "interception", "clearance"})

SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["headline", "explanation"],
    "properties": {"headline": {"type": "string", "minLength": 1},
                   "explanation": {"type": "string", "minLength": 1}},
}


# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
#
# Code decides: key events and facts
#
# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%


def _gain(ev: MatchEvent) -> float:
    """Metres a pass moved the ball toward goal (0 if coordinates are missing)."""
    if ev.start is None or ev.end is None:
        return 0.0
    return ev.end[0] - ev.start[0]


def _last_of(events: Sequence[MatchEvent], etype: str) -> MatchEvent | None:
    return next((e for e in reversed(events) if e.type == etype), None)


def pick_key_events(events: Sequence[MatchEvent]) -> list[MatchEvent]:
    """3 to 5 events in time order: the ending, the shot or goal, then the biggest gains."""
    chosen: list[MatchEvent] = [events[-1]]
    for etype in ("shot", "goal"):
        ev = _last_of(events, etype)
        if ev is not None and ev not in chosen:
            chosen.append(ev)
    chosen = chosen[:MAX_KEY_EVENTS]
    passes = sorted((e for e in events if e.type == "pass" and e not in chosen and _gain(e) > 0),
                    key=lambda e: -_gain(e))
    others = sorted((e for e in events if e not in chosen and e not in passes),
                    key=lambda e: -_gain(e))
    for ev in passes + others:
        enough = len(chosen) >= MAX_KEY_EVENTS or (
            len(chosen) >= MIN_KEY_EVENTS and ev not in passes)
        if enough:
            break
        chosen.append(ev)
    return sorted(chosen, key=lambda e: events.index(e))


def _name(roster: Roster, player_id: str | None) -> str | None:
    player = roster.by_id().get(player_id or "")
    return player.name if player else None


def compute_facts(events: Sequence[MatchEvent], roster: Roster) -> dict[str, Any]:
    """Facts for the card. Only these numbers and names may appear in the text."""
    team = events[0].team
    facts: dict[str, Any] = {
        "team_name": roster.home_name if team == roster.home else roster.away_name,
        "opponent_name": roster.away_name if team == roster.home else roster.home_name,
        "passes": sum(1 for e in events if e.type == "pass"),
        "duration_s": round(events[-1].t - events[0].t),
        "ending": events[-1].type,
        "scored": any(e.type == "goal" for e in events),
    }
    shot = _last_of(events, "shot") or _last_of(events, "goal")
    if shot is not None and shot.start is not None:
        facts["shot_dist_m"] = round(math.dist(shot.start, GOAL_CENTRE))
    if shot is not None and _name(roster, shot.player_id):
        facts["shooter"] = _name(roster, shot.player_id)
    last = events[-1]
    if _name(roster, last.player_id) and last is not shot:
        facts["ending_player"] = _name(roster, last.player_id)
        if last.type == "save":
            facts["saver"] = facts["ending_player"]
    return facts


# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
#
# Phrasing: template and LLM
#
# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%


def _template(facts: dict[str, Any]) -> tuple[str, str]:
    team, ending = facts["team_name"], facts["ending"]
    shot_like = "shot_dist_m" in facts or facts["scored"]
    if facts["scored"]:
        headline = f"Goal for {team}!"
    elif shot_like:
        headline = f"{team} created a chance"
    else:
        headline = f"{team} pushed forward"
    if facts["passes"]:
        lead = (f"{team} moved the ball through {facts['passes']} passes "
                f"in {facts['duration_s']} seconds")
    else:
        lead = f"{team} attacked for {facts['duration_s']} seconds"
    parts = [lead]
    who = facts.get("shooter", "a player")
    dist = f" from {facts['shot_dist_m']} metres" if "shot_dist_m" in facts else ""
    if facts["scored"]:
        text = f"{parts[0]}, then {who} scored{dist}."
    elif shot_like:
        text = f"{parts[0]}, then {who} shot{dist}."
        if "saver" in facts:
            text += f" {facts['saver']} saved it."
        text += " A shot is an attempt to score."
    elif ending == "foul":
        text = f"{parts[0]}, then a foul stopped the attack."
    elif ending in WON_BALL_TYPES:
        text = f"{parts[0]}, then {facts['opponent_name']} won the ball back."
    elif ending == "out":
        text = f"{parts[0]}, then the ball went out of play."
    else:
        text = f"{parts[0]}, then the move ran out of steam."
    return headline, text


def _llm_text(facts: dict[str, Any], card_id: str, match_id: str) -> tuple[str, str] | None:
    prompt = llm.render_prompt(AGENT, {"facts": facts})
    reply = llm.get_client().complete_json(AGENT, prompt, match_id, card_id, SCHEMA)
    if reply is None:
        return None
    headline, explanation = reply["headline"], reply["explanation"]
    if not (llm.safe_text(headline, MAX_HEADLINE_CHARS)
            and llm.safe_text(explanation, MAX_EXPLANATION_CHARS)):
        logger.warning("catchup: %s text unsafe or too long, using template", card_id)
        llm.record(AGENT, "unsafe")
        return None
    if not llm.number_guard(f"{headline} {explanation}", facts):
        logger.warning("catchup: %s number guard failed, using template", card_id)
        llm.record(AGENT, "guard")
        return None
    return headline, explanation


def build_catchup(attack_events: Sequence[MatchEvent], roster: Roster, card_id: str,
                  match_id: str) -> dict[str, Any]:
    """Build the ``catchup.card`` payload for one finished attack.

    Args:
        attack_events: every event of the attack, in time order.
        roster: teams and players, for names.
        card_id: card id (also the LLM cache key's qid).
        match_id: part of the LLM cache key (required: "" would collide across matches).

    Returns:
        {card_id, t_start, t_end, event_ids, headline, explanation, facts, source}. ``source``
        is "llm" or "template"; the app strips it before sending the contract message.

    Raises:
        ValueError: if ``attack_events`` is empty.
    """
    if not attack_events:
        raise ValueError("attack_events is empty")
    events = list(attack_events)
    facts = compute_facts(events, roster)
    text = _llm_text(facts, card_id, match_id)
    headline, explanation = text or _template(facts)
    return {
        "card_id": card_id,
        "t_start": events[0].t,
        "t_end": events[-1].t,
        "event_ids": [e.id for e in pick_key_events(events)],
        "headline": headline,
        "explanation": explanation,
        "facts": facts,
        "source": "llm" if text else "template",
    }
