"""test_demo_lines.py — AI call lines: facts, the no-leak / number guard, cache-only timeline.

No network: clients are fake or read a temp cache; httpx.Client is patched to fail loudly.
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from backend.agents import llm
from backend.demo import lines
from backend.demo.script import build_timeline
from backend.demo.session import DemoSession
from backend.ingest.loader import load_match

M = load_match("nbr-cal-seed42")
SAKA_GOAL = 1429.976


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def boom(*_a, **_k):
        raise AssertionError("network used in a test")
    monkeypatch.setattr(httpx, "Client", boom)


def opens(tl) -> list[dict]:
    return [c.data for c in tl.cues if c.type == "call.open"]


def saka_call(tl):
    return next(c for c in tl.calls.values() if round(c.open_t, 3) == SAKA_GOAL)


def run(tl) -> list[dict]:
    sent: list[dict] = []

    async def send(env: dict) -> None:
        sent.append(env)

    async def instant(_: float) -> None:
        await asyncio.sleep(0)

    asyncio.run(DemoSession(tl, "ARS").run(send, instant))
    return sent


def test_facts_for_saka_goal(tmp_path):
    tl = build_timeline(M, client=llm.LLMClient(mode="cache", cache_dir=tmp_path))
    f = tl.lines[saka_call(tl).call_id].facts
    assert f["player"] == "Saka" and f["team"] == "Arsenal"
    assert f["answer"] == "SHOOT" and f["outcome"] == "goal"
    assert f["zone"] in ("in the box", "attacking third")
    assert f["score"] == [0, 0] and isinstance(f["minute"], str)


def test_all_facts_have_known_values(tmp_path):
    tl = build_timeline(M, client=llm.LLMClient(mode="cache", cache_dir=tmp_path))
    by_id = {k: v.facts for k, v in tl.lines.items()}
    assert len(by_id) == 6
    for f in by_id.values():
        assert f["zone"] in lines.ZONES and f["team"] in ("Arsenal", "Chelsea")
    outs = [by_id[k]["outcome"] for k in tl.calls]
    # Estêvão's pass sets up João Pedro's goal; Rice's shot misses
    assert outs == ["kept the move going", "kept the move going", "shot missed", "goal",
                    "set up a goal", "goal"]


def test_pass_that_sets_up_a_goal_names_the_scorer(tmp_path):
    tl = build_timeline(M, client=llm.LLMClient(mode="cache", cache_dir=tmp_path))
    by_player = {ln.facts["player"]: ln.facts for ln in tl.lines.values()}
    assert by_player["Estêvão"]["outcome"] == "set up a goal"
    assert by_player["Estêvão"]["scorer"] == "João Pedro"
    saka = tl.lines[saka_call(tl).call_id].facts
    assert saka["outcome"] == "goal" and "scorer" not in saka


@pytest.mark.parametrize("setup", ["He will shoot", "Time for a PASS?", "A shot is on",
                                   "Dribbling past them?", "Will he dribble?",
                                   "Saka can pass it"])
def test_leaking_setup_uses_template(tmp_path, setup):
    fake = lambda _a, _p: {"setup": setup, "explain": "Saka shot and it went in for a goal."}
    tl = build_timeline(M, client=llm.LLMClient(mode="fake", cache_dir=tmp_path, fake=fake))
    call = saka_call(tl)
    prompt = next(o["prompt"] for o in opens(tl) if o["call_id"] == call.call_id)
    assert prompt == "Saka is about to get the ball. What will he do?"
    assert tl.lines[call.call_id].explain == "Saka shot and it went in for a goal."
    assert tl.ai_lines is False


def test_number_and_length_guard_use_template(tmp_path):
    fake = lambda _a, _p: {"setup": "Saka is near goal. What now?" + "!" * 90,
                           "explain": "Saka scored in minute 99."}
    tl = build_timeline(M, client=llm.LLMClient(mode="fake", cache_dir=tmp_path, fake=fake))
    ln = tl.lines[saka_call(tl).call_id]
    assert ln.setup == "Saka is about to get the ball. What will he do?"
    assert ln.explain == "Saka chose to shoot."


def test_empty_cache_runs_on_templates(tmp_path):
    tl = build_timeline(M, client=llm.LLMClient(mode="cache", cache_dir=tmp_path))
    for o in opens(tl):
        assert o["prompt"] == f"{o['player']} is about to get the ball. What will he do?"
    assert tl.ai_lines is False
    msgs = run(tl)
    assert msgs[0]["type"] == "demo.session" and msgs[0]["data"]["ai_lines"] is False
    verbs = {"PASS": "pass", "SHOOT": "shoot", "DRIBBLE": "dribble"}
    for r in (m["data"] for m in msgs if m["type"] == "call.result"):
        player = tl.lines[r["call_id"]].facts["player"]
        assert r["explain"] == f"{player} chose to {verbs[r['answer']]}."


def test_default_timeline_never_goes_live(monkeypatch):
    monkeypatch.setenv("LLM_MODE", "live")
    monkeypatch.setenv("GITHUB_TOKEN", "x")
    tl = build_timeline(M)  # cache reads only, whatever LLM_MODE says
    assert len(tl.lines) == 6


@pytest.mark.xfail(strict=True, reason="Rice's call (16:52) is new: regenerate the cache with "
                   "LLM_MODE=live python -m backend.demo.lines, then remove this mark")
def test_committed_cache_is_used_by_default():
    # pins backend/llm_cache/demo_lines: the default timeline reads it and gets AI lines
    assert build_timeline(load_match("nbr-cal-seed42")).ai_lines is True


def test_cached_lines_are_used(tmp_path):
    client = llm.LLMClient(mode="cache", cache_dir=tmp_path)
    probe = build_timeline(M, client=client)
    for cid, ln in probe.lines.items():
        prompt = lines.prompt_for(ln.facts)
        key = llm.cache_key(lines.AGENT, prompt, M.match_id, cid)
        path = tmp_path / lines.AGENT / f"{key}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        name = ln.facts["player"]
        path.write_text(json.dumps({"match_id": M.match_id, "qid": cid, "response": {
            "setup": f"{name} is near the goal line. What happens next?",
            "explain": f"{name} made the right choice there."}}))
    tl = build_timeline(M, client=client)
    assert tl.ai_lines is True
    for o in opens(tl):
        assert o["prompt"] == f"{o['player']} is near the goal line. What happens next?"
    msgs = run(tl)
    assert msgs[0]["data"]["ai_lines"] is True
    res = [m["data"] for m in msgs if m["type"] == "call.result"]
    assert len(res) == 6 and all(r["explain"].endswith("made the right choice there.") for r in res)


def test_check_setup_rejects_answer_words():
    facts = {"answer": "SHOOT", "score": [0, 0]}
    assert lines.check_setup("Saka is in space. What will he do?", facts)
    for bad in ("Passing lane open?", "SHOOTING chance", "Big shot", "dribble time",
                "Can he strike?", "Striking range!", "Will he fire?", "Can he score?",
                "Scoring chance here", "Time to finish it?", "A cross is on", "Crossing time",
                "A teammate is free", "Teammates are open", "Will he run?", "Running at them?"):
        assert not lines.check_setup(bad, facts), bad
    for ok in ("First touch in the box. What now?", "He is in a brunt of pressure. What now?",
               "Arsenal are level at the moment. What will he do?"):
        assert lines.check_setup(ok, facts), ok
