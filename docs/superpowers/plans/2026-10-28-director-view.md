# Director View — Behind the Scenes of the Call Picker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** A judge-facing page at `/director/` that replays the judged fan-app cut (per side) and shows, second by second, how the app decided each PASS / SHOOT / DRIBBLE call: every decision moment found in the data, which were picked, which were blocked and why, the call windows, the AI-written lines, next to the pitch and a phone mock.

**Architecture:** Backend `backend/demo/director.py` + `GET /demo/director?team=ARS|CHE` returns one JSON document built from the same code the app uses (`build_timeline`, `find_moments`, `select_calls`, lines). `calls.py` gains `explain_selection` (the decision log); `select_calls` is refactored to use it so the two can never disagree. Frontend: static `frontend/director/` (plain HTML/CSS/JS), mounted at `/director/` like `/ops/`.

**Tech Stack:** Python 3.11+, FastAPI, pytest; vanilla JS + SVG.

**Spec:** user request 2026-10-28 with a sketch ("Director view: timing agent 幕后演示页"): left a top-down pitch with 22 dots; right the user's phone; bottom a timeline of the agent's signals and decisions (▲ asked, ◆ waited, ✕ blocked by a rule); ▶ play, a slider that can stop on any second. User chose: show the fan app's call picker (not the old timing agent); address `/director/`.

## Global Constraints

- **Decision log** (`calls.py`): `explain_selection(moments, half, start_t, end_t, team) -> list[Decision]`, `@dataclass(frozen=True) class Decision: call: Call; status: str; reason: str; against: str | None` (call_id it lost to). Status/reason (exact copy):
  - `"picked"` — `"Picked"`
  - `"blocked"` — `"Too close to {player}'s call ({dt:.1f} s apart, needs {MIN_GAP_S:.0f} s)"` or `"Would cover {player}'s result (needs {MIN_RESULT_S:.0f} s after it locks)"` — first rule that fails against the earliest-accepted conflicting call.
  - `"other_team"` — `"Not the fan's team"` (in range, other side).
  `select_calls` must return exactly the picked calls of `explain_selection` (existing tests unchanged and green).
- **Too-short moments:** `find_moments(match_id, min_window=MIN_WINDOW_S)`; the director calls it with `min_window=0` and marks moments with `lock_t - open_t < MIN_WINDOW_S` as status `"too_short"`, reason `"Only {w:.1f} s to answer (needs {MIN_WINDOW_S:.0f} s)"`.
- **`GET /demo/director?team=ARS|CHE`** (default ARS; unknown → 400). JSON:
  `{team, teams, duration, plays: [{title, half, start_t, end_t, at_start, at_end}], cues: [{at, type, data}] (the side's full timeline, wire format), moments: [{call_id, half, open_t, lock_t, at_open, at_lock, clock_open, clock_lock, player, team, answer, status, reason, against}], lines: {call_id: {facts, setup, explain, ai}}, movement: [{at, v}], attack: [{at_start, at_end, team}], rules: {min_window_s, min_gap_s, min_result_s, priority: ["DRIBBLE","SHOOT","PASS"]}}`. Only moments inside a Play's range are listed; `at_*` = real seconds in the cut (same mapping as the timeline). `movement`: ball speed m/s per real second inside Plays (distance between consecutive on-ball event positions / time), smoothed over 3 s. `attack`: spans where the ball is in a final third (fixed-frame x > 70 → `team: ARS`, x < 35 → `CHE`). Built once per side and cached.
- **Page** (`frontend/director/`, dark theme like the sketch, English copy): title "Director view: how the app picks each call"; one-line subtitle "Left: the pitch, 22 dots. Right: the fan's phone. Bottom: every decision moment the code found, and what it did with it. Press ▶ or drag the slider to stop on any second."; controls: ▶/❚❚, time label `"{match clock} · {real s}s"`, slider over the whole cut, team toggle Northbridge / Calder.
  - Pitch: SVG 105 × 68 with halfway line, boxes, centre circle, "final third" shading on the attacked third; 22 dots (team colours) from the latest `demo.shape` at or before t (glide between shapes); ball from `demo.event`; label of the ball holder.
  - Phone mock: scoreboard `"ARS 0–0 CHE"` + clock; during a picked call's window the call card (setup line + PASS / SHOOT / DRIBBLE, the right answer revealed after lock); for 5 s after lock the result + explain line; goal banner on `demo.goal`; otherwise "Match on…".
  - Detail panel under the phone: the moment nearest the playhead (or hovered/clicked): player, answer, status chip, reason, the facts sent to the LLM, the AI setup and explain lines, "written by gpt-4.1-mini on Azure OpenAI" if `ai`, else "template".
  - Timeline chart (SVG, full width under the pitch): x = 0…duration; segment boundaries with their match-clock start; movement line (blue); attack spans (orange band, colour per side); picked windows (green shading open→lock) and result-hold (grey, 3 s after lock); markers: ▲ picked at `at_open`, ✕ blocked, ◆ too short, ○ other team (small, grey); hover shows the reason; click jumps the playhead. Legend under the chart (▲ picked · ✕ blocked by a rule · ◆ too short to answer · ○ other side · green window · grey result hold · blue movement · orange attack).
- No new data, no LLM calls at runtime (lines come from the committed cache). Do not modify `ingest/`, `llm.py`, `replay.py`, `bank/`, `resolve/`, `scoring/`, `social/`, `frontend/premier-x/`, `frontend/ops-dashboard/`, `frontend/fan-app/`.
- Mount in `backend/app/main.py` exactly like `/ops` (redirect `/director` → `/director/`, before the `/` catch-all); Dockerfile copies `frontend/director/`.
- Commits `[area] Verb thing: description` + blank line + `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Branch `feature/director-view`.

## Review Focus

1. **Log and app agree** — for both sides, the `picked` moments equal the calls in that side's timeline (`build_timeline(...).calls`). Test (Task 1).
2. **Every blocked moment names a real conflicting picked call** and a reason string in the exact format. Test (Task 1).
3. **Scrubbing anywhere never breaks the page** — slider to 0, to the end, inside a jump card or half-time pause; team switch mid-play resets cleanly (Task 2 review + controller Chrome check).

---

### Task 1: Decision log and /demo/director (backend)
Files: `backend/demo/calls.py`, create `backend/demo/director.py`, `backend/demo/router.py` (route), `backend/app/main.py` (mount + redirect), `infra/Dockerfile`, tests `backend/tests/test_demo_director.py` (+ `test_demo_static.py` for the mount).
- [ ] Tests first (Review Focus 1–2; JSON shape; too-short marking; 400 on bad team; `/director` redirect), implement, full suite green, commit `[backend] Add demo director: decision log and /demo/director`.

### Task 2: Director page (frontend)
Files: create `frontend/director/index.html`, `app.js`, `styles.css`, `README.md` (owner stephenwzkong).
- [ ] Build per the constraints against the JSON spec (guard every field); `node --check`; headless check with a stand-in JSON if cheap; commit `[frontend] Add director view: pitch, phone and decision timeline`.

### Task 3: Deploy
- [ ] Build/run the image locally, Chrome check both sides (play, scrub, click markers), PR, merge, deploy v13, verify `/director/` live.
