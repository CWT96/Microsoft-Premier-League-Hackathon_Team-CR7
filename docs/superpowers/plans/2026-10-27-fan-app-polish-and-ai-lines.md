# Fan App — Polish Fixes and AI Call Lines Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Fix four things judges would notice in the 82 s cut, then add LLM-written lines to every call (a setup line when it opens, a one-line plain-English explanation when it resolves), generated once offline and served from the committed cache.

**Architecture:** Fixes live in `backend/demo/script.py`, `backend/demo/forecasts.py` and small frontend copy. AI lines: a new `backend/demo/lines.py` builds per-call facts and prompts, uses `backend/agents/llm.py` (`complete_json`, cache-first, number guard) and falls back to fixed templates; a CLI `python -m backend.demo.lines` (run once with `LLM_MODE=live GITHUB_TOKEN=$(gh auth token)`) fills `backend/llm_cache/`. The deployed app stays `LLM_MODE=cache`.

**Tech Stack:** Python 3.11, pytest, GitHub Models (`openai/gpt-4o-mini`, via existing `llm.py`); vanilla JS.

**Spec:** `docs/superpowers/specs/2026-10-27-fan-app-demo-design.md`; team doc "粉丝 App 演示：后端改动说明" — LLM option (a) chosen by the user on 2026-10-27: "每道实时预判的情景提示语，加上结果后一句新手能懂的解说", generated offline once and cached; the user approved using their `gh` login for GitHub Models. User approved the four fixes on 2026-10-27.

## Global Constraints

- New `SCRIPT` (exact): `Play(1, 691, 716, "Arsenal on the attack")`, `Play(1, 1422, 1440, "Saka's goal")`, `Pause("half_time", 10)`, `Play(2, 3600, 3616, "Chelsea hit back")`, `Play(2, 4780, 4796, "The winner")`, `Pause("full_time", 0)`. The first Play now also gets a `demo.jump` card (title + clock), like the others → 4 jumps. Expected duration **93.0 s**. Calls unchanged (6, same open/lock times and answers).
- Dribble visible: for every selected call with answer `DRIBBLE`, add at its lock time a `demo.event` `{type: "dribble", team: code, player, xy, end_xy: None}` (xy from the raw dribble `location`, 120 × 80 → 105 × 68: `x*105/120, y*68/80`, then `fixed_xy` for the away team) and a `demo.shape` with that player as holder at that xy. Frontend shows a short "DRIBBLE!" tag on the ball for ~1 s on `demo.event` type `dribble`.
- f5 forecast replaced (exact copy): key `f5`, label "NEXT 5 MIN", 500 points, question **"Who has more of the ball?"**, options `ars` "ARSENAL" / `che` "CHELSEA". Outcome: the team with more `pass` events in the window (NBR → `ars`, CAL → `che`); equal counts → `none` with text **"Even split. No points lost."** (0 points, `correct` false). f10/f15/final unchanged.
- Copy: pick screen "Kick-off as soon as you tap" → "Starts as soon as you tap".
- AI lines (exact rules): agent name `demo_lines`; one `complete_json` call per call_id asking for `{"setup": str, "explain": str}`; `setup` ≤ 90 chars, must not reveal or hint the answer, plain English for someone new to football; `explain` ≤ 110 chars, says what the player actually did and, if it led to a goal, says so. Facts per call: `player`, `team` (Arsenal/Chelsea), `minute` (clock label), `score` before the call `[home, away]`, `zone` ("own half" / "midfield" / "attacking third" / "in the box" from the fixed-frame x of the receiving pass end, for the attacking team), `answer`, `outcome` ("goal" / "shot blocked" / "shot saved" / "kept the move going" from the raw/ingested event after the lock). Number guard via `llm.number_guard` / `safe_text`; on any failure use templates: setup `"{player} is about to get the ball. What will he do?"`, explain `"{player} chose to {verb}."` (verb: pass / shoot / dribble).
- Wire: `call.open.data.prompt` = setup line (replaces the old `"{player} on the ball. What's the call?"` template); `call.result.data.explain` (new additive field) = explain line; `demo.session.data.ai_lines: bool` true when all lines came from the cache. Frontend shows `explain` under the result on the call card and a small caption "Lines written by AI from the match data" on the live screen when `ai_lines` is true.
- Generation CLI: `python -m backend.demo.lines` — builds the timeline, calls `complete_json` for each call (cache-first), prints each line and whether it passed the guard. Never runs in tests; tests use `LLM_MODE=fake` or a temp cache dir. The committed cache files live under `backend/llm_cache/demo_lines/`.
- No other behaviour change. Do not modify `backend/ingest/`, `backend/agents/llm.py`, `replay.py`, `bank/`, `resolve/`, `scoring/`, non-demo tests. Demo tests updated only for cut-specific values.
- Commits `[area] Verb thing: description` + blank line + `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Branch `backend/demo-live-calls`. Tests `.venv/bin/python -m pytest -q`.

## Review Focus

1. **The new f5 question is not free points** — over every pickable clock in the cut, both `ars` and `che` occur as outcomes. Test `test_f5_outcomes_vary` (Task 1).
2. **AI line leaks the answer** — setup must not contain "pass", "shoot", "shot", "dribble" (case-insensitive) or the answer word; enforce in code (reject → template) and test (Task 2).
3. **Cache miss on the server** — with an empty cache the demo still runs with templates and `ai_lines` false; never a network call in `cache` mode (Task 2 test).
4. **Dribble event has a position** — the injected dribble's xy is inside the pitch and the holder dot sits on it (Task 1 test).

---

### Task 1: Four polish fixes

**Files:** `backend/demo/script.py`, `backend/demo/forecasts.py`, `backend/demo/calls.py` (expose the raw dribble location on `Call` or via a helper — your choice, keep it small), `frontend/fan-app/app.js` + `index.html` (dribble tag, copy), demo tests.

- [ ] Tests first: duration 93.0; 4 jumps (first at `at == 0`); calls unchanged; one `demo.event` with type `dribble` at the DRIBBLE call's lock `at`, xy inside [0,105]×[0,68], and a `demo.shape` at the same `at` whose holder dot equals that xy; `test_f5_outcomes_vary`; f5 copy and the "Even split" text; forecast session tests re-anchored for the new Play bounds if needed. RED → implement → full suite green, demo files 3× → commit `[backend] Fix demo polish: intro card, goal tails, visible dribble, possession forecast`.

### Task 2: AI call lines

**Files:** Create `backend/demo/lines.py`, `backend/tests/test_demo_lines.py`; modify `backend/demo/script.py` / `session.py` (prompt + explain + ai_lines), `frontend/fan-app/app.js` (+css) for explain and caption; add generated `backend/llm_cache/demo_lines/*.json` (Task 3).

- [ ] Tests first (no network): facts for the Saka goal call contain player "Saka", answer "SHOOT", outcome "goal", zone "in the box" or "attacking third"; with a fake client returning a leaking setup ("He will shoot") the template is used; with an empty temp cache in `cache` mode the timeline builds, every prompt is the template and `ai_lines` is false; with a cache holding valid lines the prompt/explain come from it and `ai_lines` is true; `call.result` carries `explain`. RED → implement → green → commit `[backend] Add demo AI call lines: cached setup and explanation per call`.

### Task 3: Generate, deploy

- [ ] Controller runs `LLM_MODE=live GITHUB_TOKEN=$(gh auth token) .venv/bin/python -m backend.demo.lines`, reads all 12 lines, regenerates any that read badly (delete the cache file, rerun), commits the cache `[backend] Add demo AI lines cache: 6 calls`. Build/push `matchday-call:v6`, update, scripted live run (93 s, prompts are AI lines, `explain` present, `ai_lines` true), Chrome check, push, PR comment.
