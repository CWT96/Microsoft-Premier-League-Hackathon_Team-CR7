"""build_trace.py — writes the director view's data: the match run through detector and timing.

Last Modified: 2026-10-27 21:10 PT, first version
Inputs:  data/source/ (DEFAULT_MATCH_ID), backend/llm_cache/ (The Premier X call lines, no network)
Outputs: frontend/director/data.js (window.TRACE)

Workflow:
  - Detector.run(match) -> TimingAgent(RulePolicy()).step, one state per match second
    (the same loop as backend/agents/timing_sim.py and the policy replay.py uses by default)
  - per second: mode, situation, dull_score, threat, cost (TimingAgent.last_cost, as match.state)
    and CANDIDATE_COST, the cost at or below which timing may open a moment
  - every timing.decision (to_contract) and every Moment
  - The Premier X: each team's highlight clips (SCRIPTS) and the calls build_timeline picks

CAUTION: run from the repo root: python frontend/director/build_trace.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from backend.agents import timing_rules as TR  # noqa: E402
from backend.agents.timing import RulePolicy, TimingAgent  # noqa: E402
from backend.core.constants import DEFAULT_MATCH_ID  # noqa: E402
from backend.demo.names import TEAMS, display_name  # noqa: E402
from backend.demo.script import SCRIPTS, Play, build_timeline  # noqa: E402
from backend.detect.detector import Detector  # noqa: E402
from backend.ingest.loader import load_match  # noqa: E402

OUT_PATH = Path(__file__).resolve().parent / "data.js"


def build_trace(match_id: str = DEFAULT_MATCH_ID) -> dict:
    """Run the match through detector and timing; add The Premier X clips and calls."""
    match = load_match(match_id)
    detector = Detector(match.roster, match.roster.home, match.roster.away)
    agent = TimingAgent(RulePolicy(), match.match_id)
    cols = ("half", "t", "mode", "situation", "dull_score", "threat", "cost", "score")
    series: dict[str, list] = {k: [] for k in cols}
    seen: set[tuple[int, float]] = set()
    decisions, moments = [], []
    for state in detector.run(match):
        out = agent.step(state)
        # inputs trimmed to cost: the page shows reason / source / situation / tone
        decisions += [{**d.to_contract(), "inputs": {"cost": round(d.inputs["cost"], 3)}}
                      for d in out.decisions]
        moments += [{"moment_id": m.moment_id, "kind": m.kind, "category": m.category,
                     "half": m.half, "open_at": m.open_at, "lock_at": m.lock_at,
                     "situation": m.situation, "tone": m.tone} for m in out.moments]
        if (state.half, state.t) in seen:  # the clock stands still in the lobby and at half time
            continue
        seen.add((state.half, state.t))
        for k, v in (("half", state.half), ("t", state.t), ("mode", state.mode),
                     ("situation", state.situation), ("dull_score", round(state.dull_score, 3)),
                     ("threat", round(state.threat, 3)), ("cost", round(agent.last_cost, 3)),
                     ("score", [state.score_home, state.score_away])):
            series[k].append(v)

    premier_x = {}
    for team, script in SCRIPTS.items():
        timeline = build_timeline(match, team=team)
        premier_x[team] = {
            "clips": [{"half": p.half, "start_t": p.start_t, "end_t": p.end_t, "title": p.title}
                      for p in script if isinstance(p, Play)],
            "calls": [{"call_id": c.call_id, "half": c.half, "open_t": c.open_t,
                       "lock_t": c.lock_t, "team": TEAMS.get(c.team, {}).get("code", c.team),
                       "player": display_name(c.player_id), "answer": c.answer}
                      for c in sorted(timeline.calls.values(), key=lambda c: (c.half, c.open_t))],
        }
    return {"match_id": match.match_id, "candidate_cost": TR.CANDIDATE_COST,
            "teams": {"home": TEAMS[match.roster.home], "away": TEAMS[match.roster.away]},
            "series": series, "decisions": decisions, "moments": moments, "premier_x": premier_x}


def main() -> int:
    trace = build_trace()
    OUT_PATH.write_text("window.TRACE = " + json.dumps(trace, separators=(",", ":")) + ";\n")
    print(f"{OUT_PATH.relative_to(REPO_ROOT)}: {len(trace['series']['t'])} seconds, "
          f"{len(trace['decisions'])} decisions, {len(trace['moments'])} moments, "
          + ", ".join(f"{k} {len(v['calls'])} calls" for k, v in trace["premier_x"].items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
