"""LLM client: cache replay, fake mode, live mode through a mock transport, number guard."""

import json
import logging

import httpx
import pytest

from backend.agents import llm
from backend.agents.llm import LLMClient, cache_key, number_guard, render_prompt, safe_text

SCHEMA = {"type": "object", "required": ["message"],
          "properties": {"message": {"type": "string"}}, "additionalProperties": False}


def _call(client: LLMClient, **kw):
    args = {"agent": "explainer", "prompt": "p", "match_id": "m1", "qid": "q-1",
            "schema": SCHEMA, "timeout_s": 1.0}
    args.update(kw)
    return client.complete_json(**args)


# .............................................................................
# cache key
# .............................................................................


def test_cache_key_is_stable_and_sensitive() -> None:
    k = cache_key("explainer", "p", "m1", "q-1")
    assert k == cache_key("explainer", "p", "m1", "q-1")
    assert len(k) == 64
    for other in (("timing", "p", "m1", "q-1"), ("explainer", "p2", "m1", "q-1"),
                  ("explainer", "p", "m2", "q-1"), ("explainer", "p", "m1", "q-2")):
        assert cache_key(*other) != k


def test_cache_key_has_no_field_boundary_collision() -> None:
    assert cache_key("a", "bc", "m", "q") != cache_key("ab", "c", "m", "q")


# .............................................................................
# cache mode
# .............................................................................


def test_cache_miss_returns_none_and_never_calls_network(tmp_path, caplog) -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise AssertionError("network used in cache mode")

    client = LLMClient(mode="cache", cache_dir=tmp_path, transport=httpx.MockTransport(boom))
    with caplog.at_level(logging.WARNING):
        assert _call(client) is None
    assert "cache miss" in caplog.text


def test_cache_hit_returns_stored_response(tmp_path) -> None:
    path = tmp_path / "explainer" / f"{cache_key('explainer', 'p', 'm1', 'q-1')}.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"response": {"message": "hi"}}))
    assert _call(LLMClient(mode="cache", cache_dir=tmp_path)) == {"message": "hi"}


def test_cache_hit_failing_schema_returns_none(tmp_path, caplog) -> None:
    path = tmp_path / "explainer" / f"{cache_key('explainer', 'p', 'm1', 'q-1')}.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"response": {"wrong": 1}}))
    with caplog.at_level(logging.WARNING):
        assert _call(LLMClient(mode="cache", cache_dir=tmp_path)) is None
    assert "schema" in caplog.text


def test_corrupt_cache_file_returns_none(tmp_path) -> None:
    path = tmp_path / "explainer" / f"{cache_key('explainer', 'p', 'm1', 'q-1')}.json"
    path.parent.mkdir(parents=True)
    path.write_text("{not json")
    assert _call(LLMClient(mode="cache", cache_dir=tmp_path)) is None


def test_default_mode_is_cache(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("LLM_MODE", raising=False)
    assert LLMClient(cache_dir=tmp_path).mode == "cache"
    monkeypatch.setenv("LLM_MODE", "live")
    assert LLMClient(cache_dir=tmp_path).mode == "live"


def test_unknown_mode_logs_error_and_falls_back_to_cache(tmp_path, caplog) -> None:
    with caplog.at_level(logging.ERROR):
        client = LLMClient(mode="turbo", cache_dir=tmp_path)
    assert client.mode == "cache" and "turbo" in caplog.text


# .............................................................................
# fake mode
# .............................................................................


def test_fake_mode_returns_parsed_json(tmp_path) -> None:
    seen = []

    def fake(agent: str, prompt: str) -> str:
        seen.append((agent, prompt))
        return '{"message": "ok"}'

    client = LLMClient(mode="fake", cache_dir=tmp_path, fake=fake)
    assert _call(client) == {"message": "ok"}
    assert seen == [("explainer", "p")]
    assert not list(tmp_path.rglob("*.json"))  # fake mode never writes the cache


def test_fake_mode_schema_miss_returns_none(tmp_path, caplog) -> None:
    client = LLMClient(mode="fake", cache_dir=tmp_path, fake=lambda a, p: '{"nope": 1}')
    with caplog.at_level(logging.WARNING):
        assert _call(client) is None
    assert "schema" in caplog.text


def test_fake_mode_invalid_json_returns_none(tmp_path, caplog) -> None:
    client = LLMClient(mode="fake", cache_dir=tmp_path, fake=lambda a, p: "not json at all")
    with caplog.at_level(logging.WARNING):
        assert _call(client) is None
    assert "JSON" in caplog.text


def test_fake_mode_strips_code_fence(tmp_path) -> None:
    client = LLMClient(mode="fake", cache_dir=tmp_path,
                       fake=lambda a, p: '```json\n{"message": "ok"}\n```')
    assert _call(client) == {"message": "ok"}


def test_fake_mode_without_callable_returns_none(tmp_path) -> None:
    assert _call(LLMClient(mode="fake", cache_dir=tmp_path)) is None


def test_fake_callable_timeout_returns_none(tmp_path) -> None:
    def slow(agent: str, prompt: str) -> str:
        raise TimeoutError("too slow")

    assert _call(LLMClient(mode="fake", cache_dir=tmp_path, fake=slow)) is None


# .............................................................................
# live mode (mock transport, no real network)
# .............................................................................


def _chat_reply(content: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


def test_live_azure_writes_cache_and_hides_key(tmp_path, monkeypatch, caplog) -> None:
    monkeypatch.setenv("AZURE_AI_ENDPOINT", "https://example.invalid/v1")
    monkeypatch.setenv("AZURE_AI_API_KEY", "SECRET-KEY-123")
    monkeypatch.setenv("AZURE_AI_DEPLOYMENT", "my-deploy")
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return _chat_reply('{"message": "live"}')

    client = LLMClient(mode="live", cache_dir=tmp_path, transport=httpx.MockTransport(handler))
    with caplog.at_level(logging.DEBUG):
        assert _call(client) == {"message": "live"}
    assert str(requests[0].url) == "https://example.invalid/v1/chat/completions"
    assert json.loads(requests[0].content)["model"] == "my-deploy"
    assert "SECRET-KEY-123" not in caplog.text
    # second call is served from the cache
    requests.clear()
    assert _call(client) == {"message": "live"}
    assert not requests


def test_live_falls_back_to_github_models(tmp_path, monkeypatch) -> None:
    for k in ("AZURE_AI_ENDPOINT", "AZURE_AI_API_KEY", "AZURE_AI_DEPLOYMENT"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_x")
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _chat_reply('{"message": "gh"}')

    client = LLMClient(mode="live", cache_dir=tmp_path, transport=httpx.MockTransport(handler))
    assert _call(client) == {"message": "gh"}
    assert "models.github.ai" in str(seen[0].url)
    assert seen[0].headers["authorization"] == "Bearer ghp_x"


def test_live_without_credentials_returns_none(tmp_path, monkeypatch, caplog) -> None:
    for k in ("AZURE_AI_ENDPOINT", "AZURE_AI_API_KEY", "AZURE_AI_DEPLOYMENT", "GITHUB_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    with caplog.at_level(logging.WARNING):
        assert _call(LLMClient(mode="live", cache_dir=tmp_path)) is None
    assert "credentials" in caplog.text


@pytest.mark.parametrize("handler", [
    lambda r: httpx.Response(500, text="boom"),
    lambda r: httpx.Response(200, json={"unexpected": 1}),
    lambda r: _chat_reply("not json"),
    lambda r: _chat_reply('{"wrong": 1}'),
])
def test_live_failures_return_none_and_write_nothing(tmp_path, monkeypatch, handler) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_x")
    monkeypatch.delenv("AZURE_AI_ENDPOINT", raising=False)
    client = LLMClient(mode="live", cache_dir=tmp_path, transport=httpx.MockTransport(handler))
    assert _call(client) is None
    assert not list(tmp_path.rglob("*.json"))


def test_live_timeout_returns_none(tmp_path, monkeypatch, caplog) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_x")
    monkeypatch.delenv("AZURE_AI_ENDPOINT", raising=False)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("slow", request=request)

    client = LLMClient(mode="live", cache_dir=tmp_path, transport=httpx.MockTransport(handler))
    with caplog.at_level(logging.WARNING):
        assert _call(client) is None
    assert "request failed" in caplog.text


# .............................................................................
# number guard
# .............................................................................


@pytest.mark.parametrize("text,facts,ok", [
    ("He passed 16 metres forward.", {"dx_m": 16}, True),
    ("He passed 16 metres forward.", {"dx_m": 16.0}, True),
    ("He passed 16.0 metres.", {"dx_m": 16}, True),
    ("He passed 17 metres.", {"dx_m": 16}, False),
    ("No numbers here at all.", {}, True),
    ("27% of the room agreed.", {"crowd_forward": 27}, True),
    ("27% of the room agreed.", {"crowd_forward": 0.27}, True),
    ("28% of the room agreed.", {"crowd_forward": 0.27}, False),
    ("Shot from 18m, 3 passes.", {"a": {"shot_dist_m": 18, "b": [3, 4]}}, True),
    ("Shot from 18m, 5 passes.", {"a": {"shot_dist_m": 18, "b": [3, 4]}}, False),
    ("Ward saved at 12.5 metres.", {"d": 12.5}, True),
    ("Ward saved at 12 metres.", {"d": 12.5}, False),
    ("Over 2 goals.", {"line": "2"}, True),
    ("Over 2 goals.", {"flag": True}, False),
])
def test_number_guard(text: str, facts: dict, ok: bool) -> None:
    assert number_guard(text, facts) is ok


@pytest.mark.parametrize("text,facts,ok", [
    ("The crowd had 1,200 fans.", {"n": 1200}, True),
    ("The crowd had 1,200 fans.", {"n": 1000}, False),
    ("He moved -5 metres.", {"dx": -5}, True),
    ("He moved -5 metres.", {"dx": 5}, False),
    ("A 3-2 win.", {"a": 3, "b": 2}, True),
    ("Three passes.", {"n": 3}, False),
    ("A hundred fans.", {"n": 100}, False),
    ("Half\u00bd the room.", {"n": 1}, False),
    ("Player nbr-9 scored 2.", {"name": "nbr-9 has 77", "n": 2}, False),
    ("Player scored 2.", {"name": "nbr-9 has 77", "n": 2}, True),
    ("Player scored 77.", {"name": "nbr-9 has 77"}, False),
])
def test_number_guard_hardening(text, facts, ok) -> None:
    assert number_guard(text, facts) is ok


@pytest.mark.parametrize("text,ok", [
    ("Plain friendly text.", True),
    ("<b>bold</b>", False), ("use `code`", False), ("see https://x.io", False),
    ("visit http", False), ("a://b", False), ("line\nbreak", False), ("tab\there", False),
    ("This is void.", False), ("Avoid it", True), ("", False), ("   ", False), (None, False),
    ("x" * 11, False),
])
def test_safe_text(text, ok) -> None:
    assert safe_text(text, 10 if text == "x" * 11 else 100) is ok


# .............................................................................
# paths, live hardening, prompts, stats
# .............................................................................


@pytest.mark.parametrize("agent", ["../evil", "a/b", "Explainer", "x.y", "", "a b"])
def test_bad_agent_name_rejected_and_nothing_written(tmp_path, agent) -> None:
    client = LLMClient(mode="fake", cache_dir=tmp_path / "c", fake=lambda a, p: '{"message":"x"}')
    assert _call(client, agent=agent) is None
    assert not list(tmp_path.rglob("*.json"))


def test_qid_and_match_id_never_reach_the_path(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    monkeypatch.delenv("AZURE_AI_ENDPOINT", raising=False)
    client = LLMClient(mode="live", cache_dir=tmp_path, transport=httpx.MockTransport(
        lambda r: _chat_reply('{"message": "x"}')))
    assert _call(client, qid="../../etc/passwd", match_id="../m") == {"message": "x"}
    files = list(tmp_path.rglob("*.json"))
    assert len(files) == 1 and files[0].parent == tmp_path / "explainer"
    assert ".." not in files[0].name


def test_live_requires_https_endpoint(tmp_path, monkeypatch, caplog) -> None:
    monkeypatch.setenv("AZURE_AI_ENDPOINT", "http://insecure.invalid")
    monkeypatch.setenv("AZURE_AI_API_KEY", "k")
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    client = LLMClient(mode="live", cache_dir=tmp_path, transport=httpx.MockTransport(
        lambda r: pytest.fail("must not call")))
    with caplog.at_level(logging.WARNING):
        assert _call(client) is None
    assert "https" in caplog.text


def test_half_set_azure_config_is_logged(tmp_path, monkeypatch, caplog) -> None:
    monkeypatch.setenv("AZURE_AI_ENDPOINT", "https://x.invalid")
    monkeypatch.delenv("AZURE_AI_API_KEY", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    with caplog.at_level(logging.WARNING):
        assert _call(LLMClient(mode="live", cache_dir=tmp_path)) is None
    assert "half-set" in caplog.text


def test_live_sends_max_tokens_and_does_not_follow_redirects(tmp_path, monkeypatch, caplog) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    monkeypatch.delenv("AZURE_AI_ENDPOINT", raising=False)
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if len(seen) == 1:
            assert json.loads(request.content)["max_tokens"] == llm.MAX_TOKENS
            return httpx.Response(302, headers={"location": "https://evil.invalid/"})
        pytest.fail("redirect was followed")

    client = LLMClient(mode="live", cache_dir=tmp_path, transport=httpx.MockTransport(handler))
    with caplog.at_level(logging.WARNING):
        assert _call(client) is None
    assert len(seen) == 1 and "HTTP 302" in caplog.text


def test_live_http_status_is_logged(tmp_path, monkeypatch, caplog) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    monkeypatch.delenv("AZURE_AI_ENDPOINT", raising=False)
    client = LLMClient(mode="live", cache_dir=tmp_path,
                       transport=httpx.MockTransport(lambda r: httpx.Response(429)))
    with caplog.at_level(logging.WARNING):
        assert _call(client) is None
    assert "HTTP 429" in caplog.text


def test_live_oversized_response_dropped(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    monkeypatch.delenv("AZURE_AI_ENDPOINT", raising=False)
    big = "x" * (llm.MAX_RESPONSE_BYTES + 10)
    client = LLMClient(mode="live", cache_dir=tmp_path, transport=httpx.MockTransport(
        lambda r: _chat_reply('{"message": "' + big + '"}')))
    assert _call(client) is None


@pytest.mark.parametrize("content", [None, 5, ["a"], {"a": 1}])
def test_live_non_string_content_returns_none(tmp_path, monkeypatch, caplog, content) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    monkeypatch.delenv("AZURE_AI_ENDPOINT", raising=False)
    reply = httpx.Response(200, json={"choices": [{"message": {"content": content}}]})
    client = LLMClient(mode="live", cache_dir=tmp_path,
                       transport=httpx.MockTransport(lambda r: reply))
    with caplog.at_level(logging.WARNING):
        assert _call(client) is None
    assert "not text" in caplog.text


def test_oversized_cache_file_ignored(tmp_path) -> None:
    path = tmp_path / "explainer" / f"{cache_key('explainer', 'p', 'm1', 'q-1')}.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"response": {"message": "x" * llm.MAX_CACHE_BYTES}}))
    assert _call(LLMClient(mode="cache", cache_dir=tmp_path)) is None


def test_render_prompt_is_single_pass_and_sanitised(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(llm, "PROMPT_DIR", tmp_path)
    (tmp_path / "t.prompt").write_text("A={{a}} B={{b}} C={{c}}")
    out = render_prompt("t", {"a": "{{b}}", "b": "line1\nline2", "c": {"n": "z" * 500}})
    assert out.startswith("A={{b}} B=line1 line2 C=")
    assert "z" * (llm.MAX_PROMPT_VALUE_CHARS + 1) not in out


def test_stats_count_outcomes(tmp_path) -> None:
    llm.stats.clear()
    _call(LLMClient(mode="cache", cache_dir=tmp_path))
    client = LLMClient(mode="fake", cache_dir=tmp_path, fake=lambda a, p: '{"message": "ok"}')
    _call(client)
    _call(LLMClient(mode="fake", cache_dir=tmp_path, fake=lambda a, p: '{"no": 1}'))
    _call(LLMClient(mode="fake", cache_dir=tmp_path, fake=lambda a, p: "junk"))
    assert llm.stats[("explainer", "miss")] == 1
    assert llm.stats[("explainer", "hit")] == 1
    assert llm.stats[("explainer", "schema")] == 1
    assert llm.stats[("explainer", "error")] == 1
