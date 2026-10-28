"""scoring_summary.py — one praise-only paragraph for the score card.

Last Modified: 2026-10-24 23:05, first version (template + guarded LLM summary)
Inputs:  FinalScore (code-computed), extra facts dict; backend/agents/prompts/scoring_summary.prompt
Outputs: summary string for ``score.final``

Workflow:
  - Facts = the user's real card numbers plus any extra facts the caller passes.
  - The model (agent "scoring_summary") may phrase it; the text is accepted only if every number
    is in the facts and it contains no negative wording. Otherwise a template is used.
  - In cache mode a cache miss is the normal case (it depends on each user), so the template
    runs.

CAUTION: the score card exists to win sign-ups, so the tone is praise only by design.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from backend.agents import llm
from backend.app.models import FinalScore

logger = logging.getLogger(__name__)

AGENT = "scoring_summary"
MAX_SUMMARY_CHARS = 400
MIN_STREAK_TO_MENTION = 2
CATEGORY_LABELS = {"instant": "quick calls", "forecast": "forecasts", "player": "player picks"}
NEGATIVE_RE = re.compile(
    r"\b(?:wrong|miss|missed|misses|missing|fail|failed|fails|unfortunately|poor|bad|mistake|"
    r"mistakes|lost|loss|losses|only|but|however|improve|weak|didn't|not|worse)\b",
    re.IGNORECASE)

SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["summary"],
    "properties": {"summary": {"type": "string", "minLength": 1}},
}


def build_facts(final_score: FinalScore, extra: dict[str, Any]) -> dict[str, Any]:
    """The only numbers the summary may use: the card plus caller-supplied facts."""
    facts = final_score.model_dump(by_alias=True, mode="json")
    facts["hit_rate_pct"] = round(final_score.hit_rate * 100)
    facts["terms_count"] = len(final_score.terms_learned)
    facts.update(extra)
    return facts


def _best_category(final_score: FinalScore) -> tuple[str, int, int] | None:
    """(label, correct, issued) of the category with the best hit rate, needing >=1 correct."""
    best: tuple[float, str, int, int] | None = None
    for name, label in CATEGORY_LABELS.items():
        cat = getattr(final_score.by_category, name)
        if cat.issued and cat.correct:
            rate = cat.correct / cat.issued
            if best is None or rate > best[0]:
                best = (rate, label, cat.correct, cat.issued)
    return None if best is None else best[1:]


def _template(final_score: FinalScore) -> str:
    best = _best_category(final_score)
    if best is None:
        return ("You jumped into a live match and played along with friends. "
                "That is exactly how fans start, and the next match will click faster.")
    label, correct, issued = best
    parts = [f"You nailed {correct} of {issued} {label}."]
    if final_score.best_streak >= MIN_STREAK_TO_MENTION:
        parts.append(f"Your best run was {final_score.best_streak} in a row.")
    terms = len(final_score.terms_learned)
    if terms:
        parts.append(f"You also picked up {terms} football "
                     f"{'term' if terms == 1 else 'terms'} along the way.")
    return " ".join(parts)


def summarize(final_score: FinalScore, facts: dict[str, Any]) -> tuple[str, str]:
    """Write the score-card summary (praise only).

    Args:
        final_score: the user's computed card.
        facts: extra numbers the caller allows (for example friends beaten).

    Returns:
        (text, source): a short paragraph built from the user's real numbers, and "llm" or
        "template".
    """
    allowed = build_facts(final_score, facts)
    prompt = llm.render_prompt(AGENT, {"facts": allowed})
    reply = llm.get_client().complete_json(
        AGENT, prompt, final_score.match_id, final_score.user_id, SCHEMA)
    if reply is not None:
        text = reply["summary"]
        if not llm.safe_text(text, MAX_SUMMARY_CHARS):
            logger.warning("scoring_summary: text unsafe or too long, using template")
            llm.record(AGENT, "unsafe")
        elif NEGATIVE_RE.search(text):
            logger.warning("scoring_summary: negative wording, using template")
            llm.record(AGENT, "negative")
        elif not llm.number_guard(text, allowed):
            logger.warning("scoring_summary: number guard failed, using template")
            llm.record(AGENT, "guard")
        else:
            return text, "llm"
    return _template(final_score), "template"
