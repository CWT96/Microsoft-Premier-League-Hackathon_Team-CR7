"""llm.py — one LLM client for every agent, with a replay cache in front and a number guard.

Last Modified: 2026-10-24 22:30, first version (cache / live / fake modes, number guard)
Inputs:  env LLM_MODE, AZURE_AI_ENDPOINT, AZURE_AI_API_KEY, AZURE_AI_DEPLOYMENT, GITHUB_TOKEN;
         backend/llm_cache/<agent>/<key>.json
Outputs: backend/llm_cache/<agent>/<key>.json (live mode only)

Workflow:
  - cache (default): read the cache; a miss returns None and never touches the network.
  - live: cached answers are reused; otherwise an OpenAI-compatible chat call (Azure AI
    Foundry if configured, else GitHub Models) whose answer is written to the cache.
  - fake: an injected callable stands in for the model (tests).
  - Every failure returns None and logs why at warning level. Keys are never logged.
  - llm.stats counts (agent, outcome) so silent template fallbacks are visible.

CAUTION: LLM_MODE=live must NEVER be set on the deployed container (cache mode only).
         httpx (already in requirements) is used instead of the openai SDK to keep the
         OpenAI-compatible call to one POST.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from collections import Counter
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import httpx
import jsonschema

logger = logging.getLogger(__name__)

# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
#
# Constants
#
# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

CACHE_DIR = Path(__file__).resolve().parents[1] / "llm_cache"
PROMPT_DIR = Path(__file__).resolve().parent / "prompts"
MODES = ("cache", "live", "fake")
DEFAULT_MODE = "cache"
DEFAULT_TIMEOUT_S = 8.0
GITHUB_MODELS_URL = "https://models.github.ai/inference/chat/completions"
GITHUB_MODELS_MODEL = "openai/gpt-4o-mini"
TEMPERATURE = 0.3
AGENT_RE = re.compile(r"[a-z_]+")
NUMBER_RE = re.compile(r"(?<!\w)-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?|\d+(?:\.\d+)?")
NUMBER_WORDS = ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
                "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen",
                "seventeen", "eighteen", "nineteen", "twenty", "hundred", "thousand")
NUMBER_WORD_RE = re.compile(r"\b(?:" + "|".join(NUMBER_WORDS) + r")\b|[\u00bd\u00bc\u00be]",
                            re.IGNORECASE)
PURE_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")
CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
UNSAFE_SUBSTRINGS = ("<", ">", "`", "http", "://")
FORBIDDEN_WORD_RE = re.compile(r"\bvoid\b", re.IGNORECASE)
MAX_TOKENS = 300
MAX_RESPONSE_BYTES = 32 * 1024
MAX_CACHE_BYTES = 8 * 1024
MAX_PROMPT_VALUE_CHARS = 60
OUTCOMES = ("hit", "miss", "schema", "guard", "unsafe", "negative", "error")
FENCE_RE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)
PERCENT_SCALE = 100.0
NUMBER_TOL = 1e-9

FakeFn = Callable[[str, str], "str | dict[str, Any]"]


# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
#
# Number guard
#
# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%


def _flatten_numbers(obj: Any, out: set[float]) -> None:
    """Collect numeric fact values (strings only when they are purely a number)."""
    if isinstance(obj, bool) or obj is None:
        return
    if isinstance(obj, (int, float)):
        out.add(float(obj))
    elif isinstance(obj, str):
        if PURE_NUMBER_RE.fullmatch(obj.strip()):
            out.add(float(obj))
    elif isinstance(obj, Mapping):
        for v in obj.values():
            _flatten_numbers(v, out)
    elif isinstance(obj, (list, tuple, set, frozenset)):
        for v in obj:
            _flatten_numbers(v, out)


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= NUMBER_TOL


def number_guard(text: str, facts: Mapping[str, Any]) -> bool:
    """True if every number in ``text`` appears in ``facts`` (nested numeric values).

    16 and 16.0 are the same number, "1,200" is 1200, and a leading minus belongs to the number.
    A number followed by ``%`` also matches a fact stored as a fraction (27% matches 0.27).
    Spelled-out numbers ("three", "hundred") and vulgar fractions are rejected outright, since
    they cannot be checked.
    """
    if NUMBER_WORD_RE.search(text):
        return False
    allowed: set[float] = set()
    _flatten_numbers(facts, allowed)
    for m in NUMBER_RE.finditer(text):
        value = float(m.group().replace(",", ""))
        is_percent = text[m.end():m.end() + 1] == "%"
        candidates = [value / PERCENT_SCALE] if is_percent else []
        if not any(_close(value, a) for a in allowed) and not any(
                _close(c, a) for c in candidates for a in allowed):
            return False
    return True


def safe_text(text: Any, max_len: int) -> bool:
    """True if ``text`` is a short plain string safe to show a user.

    Rejects non-strings, empty or overlong text, markup characters, backticks, links, control
    characters (newlines included) and the word "void".
    """
    if not isinstance(text, str) or not text.strip() or len(text) > max_len:
        return False
    lowered = text.lower()
    if any(bad in lowered for bad in UNSAFE_SUBSTRINGS) or CONTROL_RE.search(text):
        return False
    return FORBIDDEN_WORD_RE.search(text) is None


# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
#
# Client
#
# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%


stats: Counter[tuple[str, str]] = Counter()


def record(agent: str, outcome: str) -> None:
    """Count one (agent, outcome) event in ``stats`` (outcome is one of ``OUTCOMES``)."""
    stats[(agent, outcome)] += 1


def cache_key(agent: str, prompt: str, match_id: str, qid: str) -> str:
    """sha256 of (agent, prompt, match_id, qid); JSON-encoded so fields cannot blur together."""
    raw = json.dumps([agent, prompt, match_id, qid], ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _parse_json_object(raw: str | dict[str, Any]) -> dict[str, Any] | None:
    """Parse model output (optionally code-fenced) into a dict, or log and return None."""
    if isinstance(raw, dict):
        return raw
    text = raw.strip()
    fenced = FENCE_RE.match(text)
    if fenced:
        text = fenced.group(1)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        logger.warning("llm: response is not valid JSON (%s)", exc)
        return None
    if not isinstance(parsed, dict):
        logger.warning("llm: response JSON is not an object")
        return None
    return parsed


class LLMClient:
    """Cache-first LLM client. Build one per process, or per test with its own ``cache_dir``."""

    def __init__(self, mode: str | None = None, cache_dir: Path | None = None,
                 fake: FakeFn | None = None,
                 transport: httpx.BaseTransport | None = None) -> None:
        self.mode = mode or os.environ.get("LLM_MODE") or DEFAULT_MODE
        if self.mode not in MODES:
            logger.error("llm: LLM_MODE %r is not one of %s, falling back to %r",
                         self.mode, MODES, DEFAULT_MODE)
            self.mode = DEFAULT_MODE
        self.cache_dir = cache_dir or CACHE_DIR
        self._fake = fake
        self._transport = transport

    # .........................................................................

    def complete_json(self, agent: str, prompt: str, match_id: str, qid: str,
                      schema: dict[str, Any], timeout_s: float = DEFAULT_TIMEOUT_S
                      ) -> dict[str, Any] | None:
        """Return the model's JSON object for this call, or None on any failure.

        Args:
            agent: agent name ("explainer", "catchup", "scoring_summary", "timing").
            prompt: full prompt text (part of the cache key).
            match_id: match the call belongs to (part of the cache key).
            qid: question / card / user id the call belongs to (part of the cache key).
            schema: JSON Schema the response must satisfy.
            timeout_s: live-call timeout in seconds.

        Returns:
            The validated dict, or None (the caller then uses its template).
        """
        path = self._cache_path(agent, cache_key(agent, prompt, match_id, qid))
        if path is None:
            record(agent, "error")
            return None
        if self.mode == "cache":
            return self._read_cache(path, schema, agent, qid)
        if self.mode == "fake":
            raw = self._call_fake(agent, prompt)
        else:
            if path.is_file():  # live mode spends nothing on a call it has already paid for
                hit = self._read_cache(path, schema, agent, qid)
                if hit is not None:
                    return hit
            raw = self._call_live(prompt, timeout_s)
        if raw is None:
            record(agent, "error")
            return None
        parsed = _parse_json_object(raw)
        if parsed is None:
            record(agent, "error")
            return None
        if not self._valid(parsed, schema, agent, qid):
            return None
        if self.mode == "live":
            self._write_cache(path, match_id, qid, parsed)
        record(agent, "hit")
        return parsed

    # .........................................................................

    def _cache_path(self, agent: str, key: str) -> Path | None:
        """Cache file for this call. Only the hash reaches the path; agent is whitelisted."""
        if not AGENT_RE.fullmatch(agent):
            logger.warning("llm: agent name %r rejected (letters and underscores only)", agent)
            return None
        root = self.cache_dir.resolve()
        path = (root / agent / f"{key}.json").resolve()
        if not path.is_relative_to(root):
            logger.warning("llm: cache path for agent %r escapes the cache dir", agent)
            return None
        return path

    @staticmethod
    def _valid(obj: dict[str, Any], schema: dict[str, Any], agent: str, qid: str) -> bool:
        try:
            jsonschema.validate(obj, schema)
        except jsonschema.ValidationError as exc:
            logger.warning("llm: %s/%s response fails schema (%s)", agent, qid, exc.message)
            record(agent, "schema")
            return False
        return True

    def _read_cache(self, path: Path, schema: dict[str, Any], agent: str, qid: str
                    ) -> dict[str, Any] | None:
        if not path.is_file():
            logger.warning("llm: cache miss for %s/%s, using template", agent, qid)
            record(agent, "miss")
            return None
        try:
            if path.stat().st_size > MAX_CACHE_BYTES:
                logger.warning("llm: cache file %s is over %d bytes, ignored", path.name,
                               MAX_CACHE_BYTES)
                record(agent, "error")
                return None
            stored = json.loads(path.read_text(encoding="utf-8"))["response"]
        except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
            logger.warning("llm: unreadable cache file %s (%s)", path.name, exc)
            record(agent, "error")
            return None
        if not isinstance(stored, dict) or not self._valid(stored, schema, agent, qid):
            return None
        record(agent, "hit")
        return stored

    def _write_cache(self, path: Path, match_id: str, qid: str, response: dict[str, Any]
                     ) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = {"match_id": match_id, "qid": qid, "response": response}
            text = json.dumps(payload, indent=2, ensure_ascii=False)
            if len(text.encode("utf-8")) > MAX_CACHE_BYTES:
                logger.warning("llm: response too large to cache, skipped")
                return
            path.write_text(text, encoding="utf-8")
        except OSError as exc:
            logger.warning("llm: could not write cache %s (%s)", path.name, exc)

    def _call_fake(self, agent: str, prompt: str) -> str | dict[str, Any] | None:
        if self._fake is None:
            logger.warning("llm: fake mode without an injected callable")
            return None
        try:
            return self._fake(agent, prompt)
        except (TimeoutError, OSError, ValueError) as exc:
            logger.warning("llm: fake callable failed (%s)", exc)
            return None

    def _endpoint(self) -> tuple[str, dict[str, str], str] | None:
        """(url, headers, model) from the environment, or None without usable credentials."""
        env = os.environ
        azure_url, azure_key = env.get("AZURE_AI_ENDPOINT"), env.get("AZURE_AI_API_KEY")
        if bool(azure_url) != bool(azure_key):
            logger.warning("llm: Azure config is half-set (need endpoint and key), ignoring it")
        elif azure_url and azure_key:
            if not azure_url.startswith("https://"):
                logger.warning("llm: AZURE_AI_ENDPOINT must start with https://, ignoring it")
            else:
                url = azure_url.rstrip("/") + "/chat/completions"
                return url, {"api-key": azure_key}, env.get("AZURE_AI_DEPLOYMENT", "")
        if env.get("GITHUB_TOKEN"):
            headers = {"Authorization": f"Bearer {env['GITHUB_TOKEN']}"}
            return GITHUB_MODELS_URL, headers, GITHUB_MODELS_MODEL
        return None

    def _call_live(self, prompt: str, timeout_s: float) -> str | None:
        endpoint = self._endpoint()
        if endpoint is None:
            logger.warning("llm: live mode but no usable credentials (Azure or GitHub token)")
            return None
        url, headers, model = endpoint
        body = {"model": model, "temperature": TEMPERATURE, "max_tokens": MAX_TOKENS,
                "response_format": {"type": "json_object"},
                "messages": [{"role": "user", "content": prompt}]}
        try:
            with httpx.Client(transport=self._transport, timeout=timeout_s,
                              follow_redirects=False) as http:
                with http.stream("POST", url, headers=headers, json=body) as resp:
                    resp.raise_for_status()
                    buf = bytearray()
                    for chunk in resp.iter_bytes():
                        buf += chunk
                        if len(buf) > MAX_RESPONSE_BYTES:
                            logger.warning("llm: response over %d bytes, dropped",
                                           MAX_RESPONSE_BYTES)
                            return None
            content = json.loads(bytes(buf))["choices"][0]["message"]["content"]
        except httpx.HTTPStatusError as exc:
            logger.warning("llm: request failed with HTTP %d", exc.response.status_code)
            return None
        except httpx.HTTPError as exc:
            logger.warning("llm: request failed (%s)", type(exc).__name__)
            return None
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            logger.warning("llm: unexpected response shape (%s)", type(exc).__name__)
            return None
        if not isinstance(content, str):
            logger.warning("llm: reply content is %s, not text", type(content).__name__)
            return None
        return content


def _clean_strings(obj: Any) -> Any:
    """Copy of ``obj`` with control characters stripped and strings capped in length."""
    if isinstance(obj, str):
        return CONTROL_RE.sub(" ", obj)[:MAX_PROMPT_VALUE_CHARS]
    if isinstance(obj, Mapping):
        return {_clean_strings(k): _clean_strings(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set, frozenset)):
        return [_clean_strings(v) for v in obj]
    return obj


def render_prompt(name: str, values: Mapping[str, Any]) -> str:
    """Fill ``prompts/<name>.prompt``: each ``{{key}}`` becomes ``values[key]``.

    One pass, so a value containing ``{{other}}`` is never expanded again. Strings in values
    lose control characters and are capped at ``MAX_PROMPT_VALUE_CHARS``. Non-string values
    are JSON-encoded (sorted keys) so the prompt, and so the cache key, is stable.
    """
    text = (PROMPT_DIR / f"{name}.prompt").read_text(encoding="utf-8")
    rendered = {k: _clean_strings(v) if isinstance(v, str) else json.dumps(
        _clean_strings(v), sort_keys=True, ensure_ascii=False) for k, v in values.items()}
    return re.sub(r"\{\{(\w+)\}\}", lambda m: rendered.get(m.group(1), m.group(0)), text)


# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
#
# Process-wide default client
#
# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

_default_client: LLMClient | None = None


def get_client() -> LLMClient:
    """The shared client, created from the environment on first use."""
    global _default_client
    if _default_client is None:
        _default_client = LLMClient()
    return _default_client


def set_client(client: LLMClient | None) -> None:
    """Replace the shared client (tests, or the app at start-up). None resets it."""
    global _default_client
    _default_client = client


def complete_json(agent: str, prompt: str, match_id: str, qid: str, schema: dict[str, Any],
                  timeout_s: float = DEFAULT_TIMEOUT_S) -> dict[str, Any] | None:
    """Module-level shortcut for ``get_client().complete_json(...)``."""
    return get_client().complete_json(agent, prompt, match_id, qid, schema, timeout_s)
