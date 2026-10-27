"""AI call lines: per PASS / SHOOT / DRIBBLE call, a setup line (call opens) and an explain line.

Code decides the facts (player, team, minute, score, zone, answer, outcome); the LLM (agent
``demo_lines``) only phrases them. A setup that hints at the answer, or any text that fails
``llm.safe_text`` / ``llm.number_guard``, falls back to the template. ``build_timeline`` calls
``build_lines`` with a cache-only client, so a running demo never touches the network.

Generate the committed cache (controller only, never in tests):
    LLM_MODE=live GITHUB_TOKEN=$(gh auth token) .venv/bin/python -m backend.demo.lines
A rejected reply is still cached (llm.py caches any schema-valid reply); ``--drop-rejected``
deletes those cache files so the next live run asks again.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from typing import Any

from backend.agents import llm
from backend.core.types import Match, MatchEvent
from backend.demo.calls import Call
from backend.demo.names import TEAMS, clock_label, display_name, fixed_xy

AGENT = "demo_lines"
MAX_SETUP_CHARS = 90
MAX_EXPLAIN_CHARS = 110
SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["setup", "explain"],
    "properties": {"setup": {"type": "string", "minLength": 1},
                   "explain": {"type": "string", "minLength": 1}},
}
ZONES = ("own half", "midfield", "attacking third", "in the box")
VERBS = {"PASS": "pass", "SHOOT": "shoot", "DRIBBLE": "dribble"}
LEAK_RE = re.compile(r"pass|shoot|shot|dribbl", re.IGNORECASE)  # any form of an answer word
HOME = "NBR"
HALF_X, THIRD_X = 52.5, 70.0
BOX_X, BOX_Y0, BOX_Y1 = 88.5, 13.84, 54.16  # penalty box in the 105 x 68 frame
GOAL_FOLLOW_S = 10.0  # a goal this soon after a pass / dribble, before the other team touches it
SHOT_OUTCOMES = {"Goal": "goal", "Blocked": "shot blocked", "Saved": "shot saved"}


@dataclass(frozen=True)
class CallLines:
    facts: dict
    setup: str
    explain: str
    setup_ok: bool  # the setup came from the model and passed every guard
    explain_ok: bool
    reply: dict | None = None  # the model's raw reply (shown by the CLI when a guard rejects it)

    @property
    def ai(self) -> bool:
        return self.setup_ok and self.explain_ok


# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
# Facts (code decides)
# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%


def zone(call: Call) -> str:
    """Where he gets the ball, for the attacking team (opening pass's end, fixed frame)."""
    xy = fixed_xy(call.team, call.open_xy)
    if xy is None:
        return "midfield"
    x = xy[0] if call.team == HOME else 105 - xy[0]  # home attacks to the right
    if x >= BOX_X and BOX_Y0 <= xy[1] <= BOX_Y1:
        return "in the box"
    return "attacking third" if x >= THIRD_X else "midfield" if x >= HALF_X else "own half"


def outcome(call: Call, events: tuple[MatchEvent, ...] | list[MatchEvent]) -> str:
    """What came of his action: a shot's result, or a goal the move led to, else the move went on."""
    if call.answer == "SHOOT":
        return SHOT_OUTCOMES.get(call.lock_outcome or "", "shot missed")
    after = sorted((e for e in events if e.half == call.half
                    and call.lock_t <= e.t <= call.lock_t + GOAL_FOLLOW_S), key=lambda e: e.t)
    for e in after:
        if e.team != call.team:
            break
        if e.type == "goal":
            return "goal"
    if call.lock_outcome and call.lock_outcome != "Complete":  # a failed pass or dribble
        return "lost the ball"
    return "kept the move going"


def call_facts(call: Call, match: Match) -> dict:
    goals = [g for g in match.events if g.type == "goal" and (g.half, g.t) <= (call.half, call.open_t)]
    return {"player": display_name(call.player_id), "team": TEAMS[call.team]["name"],
            "minute": clock_label(call.half, call.open_t),
            "score": [sum(g.team == HOME for g in goals), sum(g.team != HOME for g in goals)],
            "zone": zone(call), "answer": call.answer, "outcome": outcome(call, match.events)}


# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
# Lines (LLM phrases, guards decide)
# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%


def template_setup(facts: dict) -> str:
    return f"{facts['player']} is about to get the ball. What will he do?"


def template_explain(facts: dict) -> str:
    return f"{facts['player']} chose to {VERBS[facts['answer']]}."


def check_setup(text: Any, facts: dict) -> bool:
    """Short, safe, number-guarded and never naming (or hinting at) the answer."""
    if not llm.safe_text(text, MAX_SETUP_CHARS) or not llm.number_guard(text, facts):
        return False
    lowered = text.lower()
    return LEAK_RE.search(text) is None and str(facts.get("answer", "")).lower() not in lowered


def check_explain(text: Any, facts: dict) -> bool:
    return llm.safe_text(text, MAX_EXPLAIN_CHARS) and llm.number_guard(text, facts)


def prompt_for(facts: dict) -> str:
    return llm.render_prompt(AGENT, {"facts": facts})


def lines_for(call: Call, match: Match, client: llm.LLMClient) -> CallLines:
    """Setup and explain for one call: the model's lines where they pass, templates otherwise."""
    facts = call_facts(call, match)
    reply = client.complete_json(AGENT, prompt_for(facts), match.match_id, call.call_id, SCHEMA)
    setup = reply.get("setup") if reply else None
    explain = reply.get("explain") if reply else None
    setup_ok = reply is not None and check_setup(setup, facts)
    explain_ok = reply is not None and check_explain(explain, facts)
    if reply is not None and not (setup_ok and explain_ok):
        llm.record(AGENT, "guard")
    return CallLines(facts, setup if setup_ok else template_setup(facts),
                     explain if explain_ok else template_explain(facts), setup_ok, explain_ok, reply)


def build_lines(match: Match, calls: list[Call], client: llm.LLMClient | None = None
                ) -> dict[str, CallLines]:
    """Lines per call_id. The default client reads the committed cache only (no network)."""
    client = client or llm.LLMClient(mode="cache")
    return {c.call_id: lines_for(c, match, client) for c in calls}


def main() -> None:
    """Build the timeline with the env client (cache-first) and print every call's lines."""
    from backend.demo.script import build_timeline  # script imports this module
    from backend.ingest.loader import load_match

    client = llm.get_client()
    print(f"LLM_MODE={client.mode}  cache={client.cache_dir / AGENT}")
    match = load_match("nbr-cal-seed42")
    tl = build_timeline(match, client=client)
    for cid, ln in tl.lines.items():
        print(f"\n{cid}\n  facts:   {ln.facts}")
        print(f"  setup:   {ln.setup!r}  [{'AI ok' if ln.setup_ok else 'TEMPLATE'}]")
        print(f"  explain: {ln.explain!r}  [{'AI ok' if ln.explain_ok else 'TEMPLATE'}]")
        if ln.reply is not None and not ln.ai:
            print(f"  rejected reply: {ln.reply}")
    print(f"\nai_lines={tl.ai_lines}")
    if "--drop-rejected" in sys.argv[1:]:
        for cid, ln in tl.lines.items():
            if not ln.ai:
                key = llm.cache_key(AGENT, prompt_for(ln.facts), match.match_id, cid)
                path = client.cache_dir / AGENT / f"{key}.json"
                if path.is_file():
                    path.unlink()
                    print(f"dropped {cid} ({path.name})")


if __name__ == "__main__":
    main()
