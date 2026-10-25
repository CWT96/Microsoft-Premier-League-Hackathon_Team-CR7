"""explainer.py — phrase a question result for a newcomer; LLM text only if it passes the guard.

Last Modified: 2026-10-24 22:40, first version (template fallback + number guard)
Inputs:  Outcome facts (code-decided), Question, id -> display-name map;
         backend/agents/prompts/explainer.prompt
Outputs: dict {message, explanation, new_term, source} (source is stripped by the app)

Workflow:
  - Void outcomes get a playful template line (no model call, never the word "void").
  - Resolved outcomes: ask the model (agent "explainer"); accept only valid, short text whose
    numbers all appear in outcome.facts or question.params and that passes llm.safe_text.
  - Otherwise build a template from the facts. The model never changes a result or number.

CAUTION: facts keys are whatever the resolver emits; templates use a key only if present.
"""

from __future__ import annotations

import logging
import zlib
from collections.abc import Mapping
from typing import Any

from backend.agents import llm
from backend.app.models import Question
from backend.core.types import Outcome

logger = logging.getLogger(__name__)

AGENT = "explainer"
MAX_MESSAGE_CHARS = 80
MAX_EXPLANATION_CHARS = 240
MAX_TERM_CHARS = 40

SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["message", "explanation"],
    "properties": {
        "message": {"type": "string", "minLength": 1},
        "explanation": {"type": "string", "minLength": 1},
        "new_term": {"type": ["string", "null"]},
    },
}

VOID_LINES = (
    "Plot twist! Nobody saw that coming.",
    "Curveball! The match did its own thing.",
    "Well, that one went sideways. No harm done.",
    "The game had other plans. On to the next one!",
)
VOID_EXPLANATION = "That one does not count for anyone, so your streak is safe."

_GENERIC = ("That is what the match showed.", None)
TERMS: dict[str, str] = {
    "next_pass_dir": "forward pass",
    "keep_ball": "possession",
    "reach_final_third": "final third",
    "shot_soon": "shot",
    "goals_over": "total goals",
    "team_wins": "full time",
    "card_second_half": "yellow card",
    "second_half_goal": "second half",
    "player_touch_window": "touch",
}
STAT_TERMS = {"saves": "save", "clearances": "clearance", "completed passes": "completed pass",
              "shots": "shot"}


# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
#
# Templates
#
# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%


def _fmt(value: Any) -> str:
    """Number as it appears in facts: 16.0 -> '16', 12.5 -> '12.5'."""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _label(question: Question, option_id: str | None) -> str | None:
    for opt in question.options:
        if opt.id == option_id:
            return opt.label
    return None


def _template_explanation(outcome: Outcome, question: Question, names: Mapping[str, str]
                          ) -> tuple[str, str | None]:
    """(explanation, new_term) built from facts only."""
    facts, tid, yes = outcome.facts, question.template_id, outcome.correct_option == "yes"
    who = names.get(question.player_id or "", "") or "the player"
    if tid == "next_pass_dir":
        dx = facts.get("dx_m")
        dist = f" It moved the ball {_fmt(abs(dx))} metres." if isinstance(dx, (int, float)) else ""
        if outcome.correct_option == "forward":
            return ("A forward pass sends the ball toward the opponent's goal." + dist,
                    "forward pass")
        return ("A back pass keeps the ball safe by moving away from the goal." + dist,
                "back pass")
    if tid == "keep_ball":
        if yes:
            return "They kept possession, which means the ball stayed with the same team.", \
                TERMS[tid]
        return "They lost possession, so the other team now gets to attack.", TERMS[tid]
    if tid == "reach_final_third":
        base = ("The final third is the stretch of pitch closest to the goal they attack. "
                "Getting there is how a team creates chances.")
        return base, TERMS[tid]
    if tid == "shot_soon":
        base = "A shot is an attempt to score, and it is the clearest sign a team is dangerous."
        return base, TERMS[tid]
    if tid == "goals_over":
        goals = facts.get("goals")
        tail = f" The match had {_fmt(goals)} goals." if isinstance(goals, (int, float)) else ""
        return "Total goals add up both teams' goals over the whole match." + tail, TERMS[tid]
    if tid == "team_wins":
        return "Full time is the final whistle, and the team with more goals wins.", TERMS[tid]
    if tid == "card_second_half":
        return ("A yellow card is a warning for a foul and a red card sends the player off.",
                TERMS[tid])
    if tid == "second_half_goal":
        return "The second half is the last 45 minutes, when matches are often decided.", TERMS[tid]
    if tid == "player_stat_over":
        stat = str(question.params.get("stat", ""))
        count = facts.get("count")
        tail = f" That was {_fmt(count)}." if isinstance(count, (int, float)) else ""
        what = stat or "one stat"
        return (f"Each position has its own job, so {who} is judged on {what}.{tail}",
                STAT_TERMS.get(stat))
    if tid == "player_touch_window":
        return f"A touch is any time {who} plays the ball, even briefly.", TERMS[tid]
    return _GENERIC


def _template(outcome: Outcome, question: Question, names: Mapping[str, str]) -> dict[str, Any]:
    if outcome.status == "void":
        line = VOID_LINES[zlib.crc32(outcome.qid.encode("utf-8")) % len(VOID_LINES)]
        return {"message": line, "explanation": VOID_EXPLANATION, "new_term": None}
    label = _label(question, outcome.correct_option)
    message = f"{label} it was!" if label else "Here is what happened."
    explanation, term = _template_explanation(outcome, question, names)
    return {"message": message, "explanation": explanation, "new_term": term}


# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
#
# LLM path
#
# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%


def _accept(reply: dict[str, Any], allowed: Mapping[str, Any], qid: str) -> dict[str, Any] | None:
    """Return the cleaned reply if it is short, void-free and number-guarded, else None."""
    message, explanation = reply["message"], reply["explanation"]
    term = reply.get("new_term")
    if not (llm.safe_text(message, MAX_MESSAGE_CHARS)
            and llm.safe_text(explanation, MAX_EXPLANATION_CHARS)
            and (term is None or llm.safe_text(term, MAX_TERM_CHARS))):
        logger.warning("explainer: %s text unsafe or too long, using template", qid)
        llm.record(AGENT, "unsafe")
        return None
    if not llm.number_guard(f"{message} {explanation} {term or ''}", allowed):
        logger.warning("explainer: %s number guard failed, using template", qid)
        llm.record(AGENT, "guard")
        return None
    return {"message": message, "explanation": explanation, "new_term": term}


def explain(outcome: Outcome, question: Question, names: Mapping[str, str],
            match_id: str) -> dict[str, Any]:
    """Phrase one result for a newcomer.

    Args:
        outcome: what code decided (status, correct option, facts).
        question: the question that was asked.
        names: display names by player id and team code.
        match_id: part of the LLM cache key (required: "" would collide across matches).

    Returns:
        {"message", "explanation", "new_term", "source"}. ``source`` is "llm" or "template";
        the app strips it before building ``question.result``.
    """
    if outcome.status == "void":
        return {**_template(outcome, question, names), "source": "template"}
    prompt = llm.render_prompt("explainer", {
        "question": question.text, "template_id": question.template_id,
        "correct_label": _label(question, outcome.correct_option) or "",
        "names": dict(names), "facts": outcome.facts})
    reply = llm.get_client().complete_json(AGENT, prompt, match_id, outcome.qid, SCHEMA)
    if reply is not None:
        allowed = {"facts": outcome.facts, "params": question.params}
        accepted = _accept(reply, allowed, outcome.qid)
        if accepted is not None:
            return {**accepted, "source": "llm"}
    return {**_template(outcome, question, names), "source": "template"}
