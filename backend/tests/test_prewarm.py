"""Prewarm: dry-run counts, cache round trip, and proof that its keys equal a Room's keys."""

import httpx
import pytest

from backend.agents import llm, prewarm
from backend.agents.prewarm import RecordingClient, dir_size, dry_run, main, run_pass
from backend.tests.fixtures.replay_fixtures import Harness, tiny_bundle

AGENTS = ("timing", "explainer", "catchup")


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    for k in prewarm.CREDENTIAL_ENV + ("LLM_MODE", "TIMING_POLICY"):
        monkeypatch.delenv(k, raising=False)
    previous = llm._default_client
    yield
    llm.set_client(previous)


def _live_client(tmp_path, monkeypatch, **kw) -> RecordingClient:
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    return RecordingClient(mode="live", cache_dir=tmp_path, transport=prewarm._mock_transport(),
                           **kw)


def test_dry_run_counts_every_agent_and_second_pass_is_all_hits() -> None:
    first, second = dry_run(tiny_bundle())
    for agent in AGENTS:
        assert first.requests[agent] > 0, agent
    assert first.keys == second.keys
    assert second.misses == 0 and second.failures == 0 and second.hits == sum(
        second.requests.values())


def test_live_like_run_twice_gives_full_hits_the_second_time(tmp_path, monkeypatch) -> None:
    rep1 = run_pass(tiny_bundle(), _live_client(tmp_path, monkeypatch))
    files, size = dir_size(tmp_path)
    assert files == len(rep1.keys) > 0 and size > 0
    rep2 = run_pass(tiny_bundle(), RecordingClient(mode="cache", cache_dir=tmp_path))
    assert rep2.misses == 0 and rep2.failures == 0
    assert rep2.keys == rep1.keys


def test_room_keys_are_covered_by_the_prewarm_cache(tmp_path, monkeypatch) -> None:
    """A normal one-human Room in cache mode must find everything the prewarm cached."""
    run_pass(tiny_bundle(), _live_client(tmp_path, monkeypatch))
    cached = {f.stem for f in tmp_path.rglob("*.json")}
    client = RecordingClient(mode="cache", cache_dir=tmp_path)
    llm.set_client(client)
    monkeypatch.setenv("TIMING_POLICY", "llm")
    h = Harness(humans=1)
    h.play()
    asked = {(a, k) for a, k in client.requests if a in AGENTS}
    assert {a for a, _ in asked} == set(AGENTS)  # the room really exercised every agent
    assert {k for _, k in asked} <= cached


def test_room_with_different_pace_still_hits(tmp_path, monkeypatch) -> None:
    """Two humans who never answer fast still request keys that are cached."""
    run_pass(tiny_bundle(), _live_client(tmp_path, monkeypatch))
    cached = {f.stem for f in tmp_path.rglob("*.json")}
    client = RecordingClient(mode="cache", cache_dir=tmp_path)
    llm.set_client(client)
    monkeypatch.setenv("TIMING_POLICY", "llm")
    h = Harness(humans=2)
    h.play()
    asked = {k for a, k in client.requests if a in AGENTS}
    assert asked and asked <= cached


def test_player_questions_covered_for_every_roster_player(tmp_path, monkeypatch) -> None:
    client = _live_client(tmp_path, monkeypatch)
    room = prewarm.play_match(tiny_bundle(), client)
    player_qids = {q.player_id for q in room.questions.values() if q.category == "player"}
    assert player_qids == {p.player_id for p in room.roster.players}


def test_agents_option_never_calls_other_agents_live(tmp_path, monkeypatch) -> None:
    seen: list[str] = []
    real = prewarm._mock_transport()

    def spy(request: httpx.Request) -> httpx.Response:
        prompt = __import__("json").loads(request.content)["messages"][0]["content"]
        seen.append(prewarm._guess_agent(prompt))
        return real.handle_request(request)

    monkeypatch.setenv("GITHUB_TOKEN", "t")
    client = RecordingClient(mode="live", cache_dir=tmp_path, transport=httpx.MockTransport(spy),
                             live_agents=["explainer"])
    run_pass(tiny_bundle(), client)
    assert set(seen) == {"explainer"}


def test_summary_is_never_called_live(tmp_path, monkeypatch) -> None:
    run_pass(tiny_bundle(), _live_client(tmp_path, monkeypatch))
    assert not (tmp_path / "scoring_summary").exists()


# .............................................................................
# CLI
# .............................................................................


def test_cli_refuses_without_live_mode(capsys, monkeypatch) -> None:
    assert main(["--match-id", "tiny"]) == 2
    assert "LLM_MODE=live" in capsys.readouterr().err


def test_cli_refuses_without_key(capsys, monkeypatch) -> None:
    monkeypatch.setenv("LLM_MODE", "live")
    assert main([]) == 2
    assert "No usable key" in capsys.readouterr().err


def test_cli_rejects_unknown_agent(capsys) -> None:
    assert main(["--dry-run", "--agents", "bogus"]) == 2


def test_cli_dry_run_needs_no_key_and_prints_counts(capsys, monkeypatch) -> None:
    monkeypatch.setattr(prewarm, "build_bundle", lambda match_id: tiny_bundle())
    assert main(["--dry-run", "--match-id", "tiny", "--agents", "timing,explainer,catchup,summary"]
                ) == 0
    out = capsys.readouterr()
    for agent in AGENTS:
        assert agent in out.out
    assert "100 % cache hits" in out.out and "summary skipped" in out.err
