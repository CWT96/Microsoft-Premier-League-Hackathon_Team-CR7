# Fan App — Forecast Tab Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** A Forecast tab beside the live calls: three "look further ahead" forecasts (next 5 / 10 / 15 minutes) and a full-time result with a linked exact score, settled by the backend from the synthetic match events, adding to the same live points total.

**Architecture:** A pure module `backend/demo/forecasts.py` defines the forecasts, their windows and how each settles from events. `DemoSession` accepts forecast picks over the existing `/demo/ws`, anchors each window at the current match clock, and settles it the first time the replay clock passes the window's end (inside a segment, at a jump, at half time or at full time). Skipped minutes still count: settlement uses the real events in the window even when the highlights cut skipped them. The frontend adds REAL TIME / FORECAST tabs per `Forecast.dc.html`.

**Tech Stack:** Python 3.11, FastAPI WebSocket, pytest; vanilla JS.

**Spec:** `docs/superpowers/specs/2026-10-27-fan-app-demo-design.md` — decision 4 ("A separate Forecast tab the user opens any time: next 5 / 10 / 15 minutes, full-time result, linked exact score. The backend settles them."), the points table, and the builder call "Forecasts on a jumping match … offered again after each settles. The full-time result and exact score lock at half time and settle at full time." Design: `git show origin/frontend/demo-ux:frontend/demo-ux/screens/Forecast.dc.html`.

## Global Constraints

- Points (spec table, never minus): next 5 / 10 / 15 min forecast **+500 / +1,000 / +1,500** if right; full-time result **+3,000**; exact score **+2,000 extra** (only together with the right result). Wrong = 0.
- Questions and options (design copy, exact):
  - `f5` "NEXT 5 MIN" — "Will there be a corner?" — options `yes` "YES" / `no` "NO"; settles from any `corner` event (either team) in the window.
  - `f10` "NEXT 10 MIN" — "Who takes the next shot?" — options `ars` "ARSENAL" / `che` "CHELSEA"; settles from the team of the first `shot` event in the window; no shot → outcome `none` (0 points, copy "No shots in that spell. No points lost.").
  - `f15` "NEXT 15 MIN" — "Will anyone score?" — options `goal` "GOAL" / `nogoal` "NO GOAL"; settles from any `goal` event in the window.
  - `final` "FULL TIME" — "How does it end?" — options `ars` "ARSENAL" / `draw` "DRAW" / `che` "CHELSEA" + exact score (home, away), each 0–9, never below the live score; result and score are linked (picking a result adjusts the score to match; changing the score updates the result).
- Window = `[t0, min(t0 + minutes*60, half_end)]` in the half where it was picked; `t0` = match time of the latest `demo.clock` tick. `half_end` = `match.first_half_end_t` (half 1) / `match.full_end_t` (half 2). Picks only while a Play is running (after a `demo.clock` and before `demo.half_time` / `demo.full_time` / the next `demo.jump`… simplest rule: accepted only if the latest cue sent was not `demo.jump`, `demo.half_time` or `demo.full_time`). One pending pick per key; a settled key can be picked again.
- `final` can be set or changed any time before the first `demo.clock` of half 2; after that it is locked. Settles at `demo.full_time` with the final score from the timeline (2–1).
- Shared total: forecast points add to the same `total` that `call.result` reports (one running total in `DemoSession`).
- Additive protocol only: existing messages keep their shape. `demo.clock` gains an additive `t` field (match seconds, float) — nothing else changes in existing cues.
- No LLM; fixed templates. Do not modify `backend/app/replay.py`, `backend/bank/`, `backend/resolve/`, `backend/scoring/`, `backend/ingest/`, existing tests.
- Commits `[area] Verb thing: description` + blank line + `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Branch `backend/demo-live-calls`. Tests `.venv/bin/python -m pytest -q`.

## Review Focus

1. **Window crosses a skipped stretch** — picked at 01:40, the clock jumps to 23:20: the forecast settles right after the jump from real events in 01:40–06:40, never stays pending. Test: `test_window_settles_after_jump` (Task 2).
2. **Picked late in the half** — window clamps to the half end and settles at half time. Test: `test_window_clamped_to_half` (Task 1) + `test_pending_settles_at_half_time` (Task 2).
3. **Final changed after lock / garbage picks** — ignored with a `forecast.rejected`; never crashes; score below live score rejected. Test: `test_final_locked_in_second_half`, `test_bad_forecast_messages` (Task 2).
4. **Everything settles by full time** — no forecast is left pending when `demo.full_time` is sent. Test: `test_all_settled_by_full_time` (Task 2).
5. **Forecast tab open when a call pops** — the judge must not miss the call: the app switches to REAL TIME on `call.open` (Task 3 review).

---

### Task 1: Forecast rules (pure)

**Files:** Create `backend/demo/forecasts.py`; Test `backend/tests/test_demo_forecasts.py`.

**Interfaces (Produces):**
- `@dataclass(frozen=True) class ForecastDef: key: str; label: str; minutes: int; points: int; question: str; options: tuple[tuple[str, str], ...]` (option id, display label).
- `WINDOWS: tuple[ForecastDef, ...]` — f5, f10, f15 exactly as in Global Constraints.
- `FINAL_POINTS = 3000`, `EXACT_POINTS = 2000`, `FINAL_OPTIONS = (("ars","ARSENAL"),("draw","DRAW"),("che","CHELSEA"))`.
- `window(defn: ForecastDef, half: int, t0: float, half_end: float) -> tuple[float, float]`.
- `outcome(events: Sequence[MatchEvent], key: str, half: int, t0: float, t1: float) -> str` — `"yes"/"no"`, `"ars"/"che"/"none"`, `"goal"/"nogoal"`; window inclusive `t0 <= e.t <= t1`, same half; team mapping NBR → `ars`, CAL → `che`.
- `score_window(defn, pick, outcome) -> int` — `defn.points` if pick == outcome else 0.
- `result_of(home: int, away: int) -> str` — `ars`/`draw`/`che`.
- `score_final(pick_result: str, home: int, away: int, final: tuple[int, int]) -> tuple[int, int]` — (result points, exact points); exact only if the result is right and the score matches.

- [ ] **Step 1: Failing tests**

```python
from backend.ingest.loader import load_match
from backend.demo.forecasts import WINDOWS, window, outcome, score_window, score_final, result_of

M = load_match("nbr-cal-seed42")
F5, F10, F15 = WINDOWS

def test_defs():
    assert [(d.key, d.minutes, d.points) for d in WINDOWS] == [("f5",5,500),("f10",10,1000),("f15",15,1500)]
    assert [o for o, _ in F10.options] == ["ars", "che"]

def test_window_clamped_to_half():
    assert window(F15, 1, 2500.0, M.first_half_end_t) == (2500.0, M.first_half_end_t)
    assert window(F5, 1, 100.0, M.first_half_end_t) == (100.0, 400.0)

def test_outcomes_from_events():
    assert outcome(M.events, "f15", 1, 1420.0, 2320.0) == "goal"        # Saka 23:54
    assert outcome(M.events, "f10", 1, 1420.0, 2020.0) == "ars"         # Saka's shot first
    assert outcome(M.events, "f10", 1, 100.0, 700.0) == "che"           # first shot CAL at ~345
    assert outcome(M.events, "f15", 1, 100.0, 1000.0) == "nogoal"
    assert outcome(M.events, "f10", 1, 2850.0, M.first_half_end_t) == "none"

def test_scores():
    assert score_window(F5, "no", "no") == 500 and score_window(F5, "yes", "no") == 0
    assert result_of(2, 1) == "ars" and result_of(1, 1) == "draw"
    assert score_final("ars", 2, 1, (2, 1)) == (3000, 2000)
    assert score_final("ars", 3, 1, (2, 1)) == (3000, 0)
    assert score_final("draw", 1, 1, (2, 1)) == (0, 0)
```

- [ ] **Step 2:** run → FAIL. **Step 3:** implement. **Step 4:** run full suite → PASS. **Step 5:** commit `[backend] Add demo forecasts: windows, outcomes and points from synthetic events`.

---

### Task 2: Forecasts in the demo session

**Files:** Modify `backend/demo/session.py`, `backend/demo/router.py` (route new client message types to the session), `backend/demo/script.py` (additive `t` in `demo.clock` data; `Timeline` gains what the session needs to settle: the match events, `half_ends: dict[int, float]`, `final_score: tuple[int, int]` — add fields with defaults so existing constructors keep working); Test `backend/tests/test_demo_forecast_session.py`.

**Interfaces:**
- Consumes Task 1.
- Client → server (after `start`):
  - `{"type": "forecast.pick", "data": {"key": "f5"|"f10"|"f15", "pick": <option id>}}`
  - `{"type": "forecast.final", "data": {"result": "ars"|"draw"|"che", "home": int, "away": int}}` (repeatable until locked; `result` must equal `result_of(home, away)`; scores 0–9 and ≥ the live score)
- Server → client:
  - `forecast.locked {key, pick, from_clock, to_clock}` — accepted window pick (clocks via `clock_label`).
  - `forecast.final_saved {result, home, away}` — accepted final pick.
  - `forecast.rejected {key, reason}` — reason one of `"not_live"`, `"pending"`, `"bad_pick"`, `"locked"`.
  - `forecast.final_locked {result, home, away}` (or `{}` if none set) — once, at the first half-2 `demo.clock` (sent before that tick).
  - `forecast.result {key, pick, outcome, correct, points, total, to_clock, text}` — `text` templates: right → `"Forecast hit"`; wrong → `"Not this time"`; f10 `none` → `"No shots in that spell. No points lost."`.
  - `forecast.final_result {result, home, away, final: [h, a], result_points, exact_points, points, total}` — sent right before `demo.full_time`.
- `DemoSession` gains: `forecast_pick(key, pick) -> dict` and `forecast_final(result, home, away) -> dict` (return the message to send — the router sends it), pending state, and settlement in `run`: before sending each `demo.clock` (using its `half`/`t`), `demo.half_time` and `demo.full_time`, settle every pending window whose `t1 <= now` (or whose half is over). Settlement messages go out before the cue that triggered them. `total` is the one shared total (calls + forecasts).
- `demo.session` data gains additive `forecasts: {windows: [{key,label,minutes,points,question,options:[{id,label}]}], final: {points: 3000, exact_points: 2000, options: [...]}}` so the frontend renders from the server.

- [ ] **Step 1: Failing tests** (drive `DemoSession` directly with instant sleep, like `test_demo_session.py`; capture sent messages)

```python
def test_window_settles_after_jump(): ...
    # pick f15 "goal" right after the first demo.clock of the "Opening minutes" Play (t≈88):
    # window 88–988 has no goal → forecast.result {key:"f15", outcome:"nogoal", correct: False, points: 0}
    # arrives before the first demo.clock of the "Saka's goal" Play, and after that Play's demo.jump
def test_hit_adds_to_shared_total(): ...
    # pick f10 "ars" at the first tick of "Saka's goal" (t=1400) → result correct, points 1000,
    # and the next call.result's total includes the 1000
def test_pending_settles_at_half_time(): ...
    # pick f5 at the first tick of the "Half time" Play (t=2848) → settles before demo.half_time
def test_final_locked_in_second_half(): ...
    # forecast.final ars 2-1 during half 1 → final_saved; after the first half-2 tick → rejected "locked";
    # at full time final_result points 5000 (3000 + 2000), total includes it
def test_bad_forecast_messages(): ...
    # unknown key, unknown pick, result not matching score, score below live score, pick during HT pause,
    # second pick on a pending key → forecast.rejected with the right reason; nothing raises
def test_all_settled_by_full_time(): ...
    # pick all three windows at the last Play's first tick → three forecast.result before demo.full_time
def test_demo_clock_has_t(): ...
    # every demo.clock data has float t and half; existing keys unchanged
```

Write each test fully (exact assertions) from the comments; they encode the required behaviour.
- [ ] **Step 2:** FAIL. **Step 3:** implement. **Step 4:** full suite PASS (existing demo tests unchanged and green). **Step 5:** commit `[backend] Add demo forecast session: picks, settlement and shared total over /demo/ws`.

---

### Task 3: Forecast tab in the fan app

**Files:** Modify `frontend/fan-app/index.html`, `app.js`, `styles.css`.

- Tabs under the scoreboard: "REAL TIME" / "FORECAST" (design `Forecast.dc.html`). The pitch stays visible on both; the call card area swaps with the forecast panel.
- Forecast panel (render from `demo.session.forecasts`): header "BIGGER CALL, BIGGER POINTS" / "Look further ahead, earn more"; three window cards (window label, `+points`, question, two option buttons); tapping sends `forecast.pick`; on `forecast.locked` the card shows "LOCKED IN · SCORED AT {to_clock}" with the pick highlighted; on `forecast.result` show the outcome on the card for ~4 s ("Forecast hit +500" / "Not this time" / no-shot text), pop the points like a call result, update total, then the card re-opens for a new pick. On `forecast.rejected` "not_live" show a short "Opens when play resumes" note.
- Full-time card: "How does it end?" ARSENAL / DRAW / CHELSEA + exact score steppers (+/−, linked per the design's `pickResult`/`bump` logic, never below the live score); every change sends `forecast.final` (debounce ~300 ms); show "LINKED TO YOUR RESULT"; after `forecast.final_locked` disable with "LOCKED AT HALF TIME"; `forecast.final_result` shows on the full-time card and pops points.
- "Riding on your forecasts +{potential}" = sum of points of pending picks (+ final if saved and not settled).
- On `call.open` while on FORECAST: switch to REAL TIME automatically (don't let the judge miss a call). A small dot on the FORECAST tab when a forecast settles while on REAL TIME.
- Full-time card adds a line: "Forecasts: +X" (sum of forecast points).
- Same patterns as before: textContent only, stale-socket guard, reset on Play again.

- [ ] **Step 1:** implement. **Step 2:** `.venv/bin/python -m pytest -q` green; `node --check frontend/fan-app/app.js`; scripted smoke over `/demo/ws` with forecast picks. **Step 3:** commit `[frontend] Add fan app forecast tab: 5/10/15-minute forecasts and full-time result`.

---

### Task 4: Redeploy

- [ ] Build/push `matchday-call:v4`, update the container app, verify live with a scripted run that makes forecast picks and receives `forecast.result` and `forecast.final_result`; push the branch (PR #9).
