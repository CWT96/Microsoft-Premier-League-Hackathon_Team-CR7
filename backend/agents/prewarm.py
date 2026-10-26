"""prewarm.py — fill backend/llm_cache/ so a deployed demo (LLM_MODE=cache) never misses.

Last Modified: 2026-10-25 00:40, first version
Inputs:  the match dataset (via backend.app.replay.build_bundle), a real LLM key for live runs
Outputs: backend/llm_cache/<agent>/<key>.json (live runs); a summary on stdout

Workflow:
  - Order of operations for the real cache (Stephen):
      1. Merge the real generator (backend/agents/question_generator.py).
      2. Put the keys in .env (AZURE_AI_* or GITHUB_TOKEN).
      3. LLM_MODE=live python -m backend.agents.prewarm
      4. Commit backend/llm_cache/.
      5. Deploy with LLM_MODE=cache TIMING_POLICY=llm.
  - Usage: python -m backend.agents.prewarm [--match-id ID] [--agents timing,explainer,catchup]
    [--dry-run]. --dry-run needs no key: a fake model fills a temp cache, then a second pass must
    hit it 100 %.
  - It plays the match through a real ``Room`` on a fake clock with TIMING_POLICY=llm and one
    scripted member per roster player (each answers every card at once and resumes every
    pause), so prompts, and so cache keys, are built by the same code the app runs. Every
    shared question result, catch-up card and timing decision is covered, and every player's
    own player questions (roster size x 4).
  - Agents not named in --agents are read from the cache and never called live, so the replay
    flow stays the same.

CAUTION: the end-of-match summary depends on each user's answers, so it is never prewarmed
         (cache mode uses its template). Cache keys are stable only while the dataset, the
         detector, the generator, the prompts and the timing rules stay unchanged; the first
         human to pick up a moment fixes its question, so a generator that reads the detector
         state at pick-up time can still miss for a user who plays at a very different pace.
         Re-run this command after changing any of them. Never set LLM_MODE=live when deployed.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import os
import sys
import tempfile
from collections import Counter
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from backend.agents import llm
from backend.app.replay import MAX_SPEED, MatchBundle, Room, TIMING_POLICY_ENV, build_bundle
from backend.core.constants import DEFAULT_MATCH_ID

logger = logging.getLogger(__name__)

PREWARM_AGENTS = ("timing", "explainer", "catchup")
SKIPPED_AGENTS = {"summary": "per-user (depends on each user's answers); never prewarmed"}
PREWARM_TIMEOUT_S = 30.0  # the app's 1 s timing timeout would drop slow live answers
MAX_PAUSE_ROUNDS = 50
HOST_ID_PREFIX = "prewarm-"
DRY_RUN_TOKEN = "dry-run-token"
CREDENTIAL_ENV = ("AZURE_AI_ENDPOINT", "AZURE_AI_API_KEY", "AZURE_AI_DEPLOYMENT", "GITHUB_TOKEN")

# Canned model answers for --dry-run and tests, one per agent (schema-valid, no digits).
CANNED: dict[str, dict[str, Any]] = {
    "timing": {"action": "open", "tone": "Quick one!", "reason": "Calm play, nothing to miss."},
    "explainer": {"message": "Good call!", "explanation": "That is how the play unfolded.",
                  "new_term": "possession"},
    "catchup": {"headline": "A big move forward",
                "explanation": "The team pushed up the pitch and went for goal."},
    "scoring_summary": {"summary": "You played along like a fan."},
}


def canned_reply(agent: str, prompt: str) -> dict[str, Any]:
    """Schema-valid stand-in answer for ``agent`` (``prompt`` is unused, kept for fake mode)."""
    return CANNED[agent]


# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
#
# Recording client
#
# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%


class RecordingClient(llm.LLMClient):
    """LLMClient that records every request key and spends live calls only on chosen agents.

    Other agents (including the per-user summary) are served from the cache alone, never live,
    so the replay flow does not change with ``--agents``.
    """

    def __init__(self, *, live_agents: Sequence[str] = PREWARM_AGENTS, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.live_agents = frozenset(live_agents)
        self.requests: list[tuple[str, str]] = []  # (agent, cache key), in request order
        self._readonly = llm.LLMClient(mode="cache", cache_dir=self.cache_dir)

    def complete_json(self, agent: str, prompt: str, match_id: str, qid: str,
                      schema: dict[str, Any], timeout_s: float = llm.DEFAULT_TIMEOUT_S
                      ) -> dict[str, Any] | None:
        self.requests.append((agent, llm.cache_key(agent, prompt, match_id, qid)))
        if self.mode == "live":
            if agent not in self.live_agents:
                return self._readonly.complete_json(agent, prompt, match_id, qid, schema,
                                                    timeout_s)
            timeout_s = max(timeout_s, PREWARM_TIMEOUT_S)
        return super().complete_json(agent, prompt, match_id, qid, schema, timeout_s)


def _mock_transport() -> httpx.MockTransport:
    """Pretend chat endpoint that answers with ``canned_reply`` for the prompt's agent."""
    def handler(request: httpx.Request) -> httpx.Response:
        prompt = json.loads(request.content)["messages"][0]["content"]
        agent = _guess_agent(prompt)
        return httpx.Response(200, json={"choices": [
            {"message": {"content": json.dumps(canned_reply(agent, prompt))}}]})
    return httpx.MockTransport(handler)


def _guess_agent(prompt: str) -> str:
    """Which agent a prompt belongs to (dry-run only; real runs never need this)."""
    if prompt.startswith("You write the result line"):
        return "explainer"
    if prompt.startswith("You write the catch-up"):
        return "catchup"
    if prompt.startswith("You write a one-paragraph"):
        return "scoring_summary"
    return "timing"


# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
#
# Playing the match
#
# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%


@contextlib.contextmanager
def _env(**values: str | None) -> Iterator[None]:
    """Set (or unset, for None) environment variables, restoring them afterwards."""
    saved = {k: os.environ.get(k) for k in values}
    try:
        for k, v in values.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class _Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def now(self) -> float:
        return self.t


def _answer_all(room: Room) -> int:
    """Every member answers its open card with the first option. Returns answers given."""
    given = 0
    for m in room.humans:
        card = m.open
        if card is not None and card.option is None and not card.locked:
            given += int(room.answer(m.user_id, card.question.qid, card.question.options[0].id))
    return given


def play_match(bundle: MatchBundle, client: llm.LLMClient,
               propose: Callable | None = None) -> Room:
    """Play one whole match in a real Room with one scripted member per roster player.

    Args:
        bundle: the match, detector states and model.
        client: the LLM client every agent (timing, explainer, catch-up, summary) will use.
        propose: question generator; None = ``generator.get_propose()`` as in the app.

    Returns:
        The finished Room.
    """
    clock = _Clock()
    previous = llm._default_client
    llm.set_client(client)
    try:
        with _env(**{TIMING_POLICY_ENV: "llm"}):
            room = Room("prewarm", bundle, propose=propose, now=clock.now, speed=MAX_SPEED)
        players = bundle.match.roster.players
        for p in players:
            room.add_member(f"{HOST_ID_PREFIX}{p.player_id}", p.name, p.team, p.player_id,
                            lambda _msg: None)
        host = f"{HOST_ID_PREFIX}{players[0].player_id}"
        while True:
            _answer_all(room)
            delay = room.tick()
            if delay is None:
                return room
            clock.t += delay
            if room.pause is not None:
                for _ in range(MAX_PAUSE_ROUNDS):  # let everyone see every stack card first
                    if not _answer_all(room):
                        break
                room.control(host, "resume")
    finally:
        llm.set_client(previous)


# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
#
# Runs and reports
#
# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%


@dataclass
class RunReport:
    """What one pass asked of the model."""

    requests: dict[str, int] = field(default_factory=dict)  # agent -> requests
    unique: dict[str, int] = field(default_factory=dict)  # agent -> distinct cache keys
    hits: int = 0
    misses: int = 0
    failures: int = 0
    keys: set[tuple[str, str]] = field(default_factory=set)


def _report(client: RecordingClient, before: Counter) -> RunReport:
    delta = Counter(llm.stats)
    delta.subtract(before)
    rep = RunReport()
    own = [(a, k) for a, k in client.requests if a in PREWARM_AGENTS]
    rep.requests = dict(Counter(a for a, _ in own))
    rep.keys = set(own)
    rep.unique = dict(Counter(a for a, _ in rep.keys))
    mine = {kv: v for kv, v in delta.items() if kv[0] in PREWARM_AGENTS}
    rep.hits = sum(v for (_, o), v in mine.items() if o == "hit")
    rep.misses = sum(v for (_, o), v in mine.items() if o == "miss")
    rep.failures = sum(v for (_, o), v in mine.items() if o in ("schema", "error"))
    return rep


def run_pass(bundle: MatchBundle, client: RecordingClient,
             propose: Callable | None = None) -> RunReport:
    """Play the match once with ``client`` and report what was asked."""
    before = Counter(llm.stats)
    play_match(bundle, client, propose)
    return _report(client, before)


def dir_size(path: Path) -> tuple[int, int]:
    """(files, bytes) under ``path``."""
    files = [f for f in path.rglob("*.json") if f.is_file()] if path.exists() else []
    return len(files), sum(f.stat().st_size for f in files)


def dry_run(bundle: MatchBundle, propose: Callable | None = None
            ) -> tuple[RunReport, RunReport]:
    """Fake-model pass into a temp cache, then a cache-only pass that must be 100 % hits."""
    with tempfile.TemporaryDirectory() as tmp, \
            _env(**{**dict.fromkeys(CREDENTIAL_ENV), "GITHUB_TOKEN": DRY_RUN_TOKEN}):
        first = run_pass(bundle, RecordingClient(mode="live", cache_dir=Path(tmp),
                                                 transport=_mock_transport()), propose)
        second = run_pass(bundle, RecordingClient(mode="cache", cache_dir=Path(tmp)), propose)
    return first, second


def live_check() -> str | None:
    """Reason a real run is not allowed, or None."""
    if os.environ.get("LLM_MODE") != "live":
        return "LLM_MODE=live is required (the prewarm calls the real model)."
    if llm.LLMClient(mode="live")._endpoint() is None:
        return ("No usable key: set AZURE_AI_ENDPOINT (https) + AZURE_AI_API_KEY "
                "+ AZURE_AI_DEPLOYMENT, or GITHUB_TOKEN.")
    return None


def format_report(title: str, rep: RunReport) -> str:
    lines = [title]
    for agent in sorted(rep.requests):
        lines.append(f"  {agent:16s} requests {rep.requests[agent]:5d}   "
                     f"distinct keys {rep.unique.get(agent, 0):5d}")
    lines.append(f"  hits {rep.hits}  misses {rep.misses}  failures {rep.failures}")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point. Returns the process exit code."""
    ap = argparse.ArgumentParser(prog="python -m backend.agents.prewarm",
                                 description="Fill backend/llm_cache/ for a match.")
    ap.add_argument("--match-id", default=os.environ.get("MATCH_ID") or DEFAULT_MATCH_ID)
    ap.add_argument("--agents", default=",".join(PREWARM_AGENTS),
                    help="comma list of timing,explainer,catchup (summary is per-user, skipped)")
    ap.add_argument("--dry-run", action="store_true",
                    help="fake model, temp cache, no key needed; prints call counts")
    args = ap.parse_args(argv)
    names = [a.strip() for a in args.agents.split(",") if a.strip()]
    for a in names:
        if a in SKIPPED_AGENTS:
            print(f"note: {a} skipped, it is {SKIPPED_AGENTS[a]}", file=sys.stderr)
    agents = [a for a in names if a in PREWARM_AGENTS]
    unknown = [a for a in names if a not in PREWARM_AGENTS and a not in SKIPPED_AGENTS]
    if unknown or not agents:
        print(f"error: unknown or empty --agents {unknown or names}", file=sys.stderr)
        return 2
    if not args.dry_run:
        problem = live_check()
        if problem:
            print(f"error: {problem} Use --dry-run to check counts without a key.",
                  file=sys.stderr)
            return 2
    bundle = build_bundle(args.match_id)
    if args.dry_run:
        first, second = dry_run(bundle)
        print(format_report(f"dry run, pass 1 (would call the model) for {args.match_id}", first))
        print(format_report("dry run, pass 2 (cache only)", second))
        ok = second.misses == 0 and second.failures == 0 and first.keys == second.keys
        print("second run is 100 % cache hits" if ok else "FAIL: second run is not all hits")
        return 0 if ok else 1
    client = RecordingClient(mode="live", live_agents=agents)
    rep = run_pass(bundle, client)
    print(format_report(f"prewarm of {args.match_id}", rep))
    files, size = dir_size(client.cache_dir)
    print(f"cache: {files} files, {size / 1024:.0f} KB in {client.cache_dir}")
    return 0 if rep.failures == 0 else 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    raise SystemExit(main())
