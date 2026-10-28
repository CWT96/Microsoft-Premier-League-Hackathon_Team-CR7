# Fan App Step 2 — Live Calls and Points Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A judge opens the deployed URL on a phone, picks Northbridge or Calder, and plays a ~3-minute highlights cut of match `nbr-cal-seed42` (relabelled Northbridge 2–1 Calder) with live PASS / SHOOT / DRIBBLE calls and live points, all driven by the backend.

**Architecture:** A new, self-contained `backend/demo/` package sits beside the existing `Room` flow (which stays untouched). Pure functions turn the ingested match into a fixed timeline of cues (clock ticks, on-ball events, jumps, calls, half time, full time); a thin per-socket session plays the timeline over a new WebSocket `/demo/ws` and scores answers. A plain HTML/CSS/JS app in `frontend/fan-app/` is served by FastAPI at `/`.

**Tech Stack:** Python 3.11, FastAPI, Starlette `StaticFiles`, pytest + FastAPI `TestClient`; plain HTML/CSS/JS (no build step); Docker; Azure Container Apps.

**Spec:** `docs/superpowers/specs/2026-10-27-fan-app-demo-design.md`

## Global Constraints

- Deadline 2026-10-27 23:59 PT. Every task ends with `pytest` green; Task 7 redeploys.
- Do not modify `backend/app/replay.py`, `backend/bank/`, `backend/resolve/`, `backend/scoring/` or existing tests. `backend/app/main.py` gets only the router include and the static mount (Task 5, Task 6).
- No LLM calls. All copy is fixed templates.
- Match id: `nbr-cal-seed42` (`backend.ingest.loader.load_match`). Internal team codes stay `NBR` (home) / `CAL` (away); display is `NBR` Northbridge / `CAL` Calder.
- Options are exactly `"PASS"`, `"SHOOT"`, `"DRIBBLE"`, in that order.
- Points (spec table): any answered call +10; correct +100; streak of 3+ correct: +50 per step (streak 3 → +50, 4 → +100, …, i.e. `50 * (streak - 2)`). Unanswered = "Too quick!": 0 points, streak unchanged. Never minus.
- Ball always moves at 1× inside a segment; time passes only by visible jumps.
- Pitch frame: home (NBR/Northbridge) attacks left to right all match. Event coordinates are per acting team on a 105 × 68 pitch; away events convert with `(105 - x, 68 - y)`.
- Design reference: `git show origin/frontend/demo-ux:frontend/demo-ux/<path>` (screens `Main.dc.html`, `Pick.dc.html`, `Live.dc.html`, `HUD.dc.html`; crests `assets/crests/nbr.svg`, `assets/crests/cal.svg`). Replicate colours, fonts, spacing, copy; do not copy the canvas runtime.
- Commits: `[area] Verb thing: description`, ending with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Work on branch `backend/demo-live-calls` (Tasks 1–5) and `frontend/fan-app` (Tasks 6–7) or one branch `demo/live-calls` if executing in one session.
- Tests run with Python 3.11: `.venv/bin/python -m pytest -q`.

## Review Focus

1. **Judge closes or refreshes the tab mid-demo** → the session task is cancelled, no exception logged, a fresh connect starts a new demo from kickoff. Test: `test_disconnect_cancels_session` (Task 5).
2. **Answer arrives twice, after lock, or for an unknown/closed call** → ignored; points counted once. Test: `test_answer_after_lock_ignored`, `test_double_answer_counts_once` (Task 5).
3. **Judge never taps** → every call resolves "Too quick!", total stays 0, demo still reaches full time. Test: `test_no_answers_reaches_full_time` (Task 5).
4. **Many judges at once on 0.5 CPU** → demo sockets capped at `MAX_DEMO_SESSIONS` (default 50); over the cap the socket closes with code 1013. Test: `test_session_cap` (Task 5).
5. **Garbage from the client** (non-JSON, wrong type, unknown pick) → ignored or socket closed with 1008; server never crashes. Test: `test_bad_messages` (Task 5).

---

### Task 1: Relabel, pitch frame and clock label

**Files:**
- Create: `backend/demo/__init__.py` (empty), `backend/demo/names.py`
- Test: `backend/tests/test_demo_names.py`

**Interfaces:**
- Produces:
  - `TEAMS: dict[str, dict[str, str]]` = `{"NBR": {"code": "NBR", "name": "Northbridge", "crest": "assets/crests/nbr.svg"}, "CAL": {"code": "CAL", "name": "Calder", "crest": "assets/crests/cal.svg"}}`
  - `PLAYERS: dict[str, tuple[str, int]]` — player_id → (display name, shirt number); exact table below.
  - `display_name(player_id: str | None) -> str` — mapped name; unknown or None → `""`.
  - `fixed_xy(team: str, xy: tuple[float, float] | None, home: str = "NBR") -> tuple[float, float] | None` — home unchanged, away `(105 - x, 68 - y)`, None passes through; round to 1 decimal.
  - `clock_label(half: int, t: float) -> str` — `"MM:SS"`; first-half stoppage (`half == 1 and t >= 2700`) → `"45+M:SS"`; second-half stoppage (`half == 2 and t >= 5400`) → `"90+M:SS"`.

Player table (synthetic id → design name, number). Players not listed stay off the sheet (never on the ball in the demo segments or unused subs).

| id | name | # | id | name | # |
|---|---|---|---|---|---|
| 910101 | Lindqvist | 1 | 910201 | Ward | 1 |
| 910102 | Achterberg | 4 | 910202 | Oyelaran | 24 |
| 910105 | Hale | 2 | 910204 | Reid | 3 |
| 910104 | Duarte | 6 | 910205 | Zelek | 6 |
| 910103 | Fairbanks | 33 | 910203 | Grigg | 21 |
| 910108 | Okafor | 41 | 910206 | Demir | 25 |
| 910106 | Pryce | 36 | 910208 | Penhale | 8 |
| 910110 | Sabatier | 8 | 910210 | Venn | 10 |
| 910109 | Mensah | 7 | 910207 | Whitcombe | 7 |
| 910107 | Quintero | 14 | 910209 | Doyle | 9 |
| 910111 | Rook | 10 | 910211 | Ortega | 41 |
| 910119 | Raman | 29 | 910215 | Morrow | 27 |
| 910120 | Thistle | 12 | 910214 | Lindgren | 12 |
| 910115 | Brandt | 11 | 910223 | Blackwood | 45 |
| 910113 | Ashdown | 23 | 910219 | Hart | 11 |
| 910122 | Bruno Guimarães | 39 | | | |

- [ ] **Step 1: Write the failing tests**

```python
from backend.demo.names import TEAMS, PLAYERS, display_name, fixed_xy, clock_label

def test_scorers_relabelled():
    assert display_name("910109") == "Mensah"
    assert display_name("910209") == "Doyle"
    assert display_name("910105") == "Hale"

def test_unknown_player_is_blank():
    assert display_name("999") == "" and display_name(None) == ""

def test_teams():
    assert TEAMS["NBR"]["name"] == "Northbridge" and TEAMS["CAL"]["code"] == "CAL"

def test_every_mapped_number_unique_per_team():
    ars = [n for pid, (_, n) in PLAYERS.items() if pid.startswith("9101")]
    che = [n for pid, (_, n) in PLAYERS.items() if pid.startswith("9102")]
    assert len(ars) == len(set(ars)) and len(che) == len(set(che))

def test_fixed_frame():
    assert fixed_xy("NBR", (90.0, 30.0)) == (90.0, 30.0)
    assert fixed_xy("CAL", (90.0, 30.0)) == (15.0, 38.0)
    assert fixed_xy("CAL", None) is None

def test_clock_label():
    assert clock_label(1, 1434.26) == "23:54"
    assert clock_label(1, 2760) == "45+1:00"
    assert clock_label(2, 3613.2) == "60:13"
    assert clock_label(2, 5667.9) == "90+4:27"
```

- [ ] **Step 2: Run to verify failure** — `.venv/bin/python -m pytest backend/tests/test_demo_names.py -q` → FAIL (ModuleNotFoundError).
- [ ] **Step 3: Implement `backend/demo/names.py`** with the table above and the four signatures.
- [ ] **Step 4: Run** — same command → PASS.
- [ ] **Step 5: Commit** — `[backend] Add demo relabel: Northbridge vs Calder names, fixed pitch frame, clock label`.

---

### Task 2: Decision moments and call selection

**Files:**
- Create: `backend/demo/calls.py`
- Test: `backend/tests/test_demo_calls.py`

**Interfaces:**
- Consumes: `backend.core.types.Match`, `MatchEvent` (fields `id, t, half, team, type, player_id, start, end, outcome`); `load_match("nbr-cal-seed42")`.
- Produces:
  - `@dataclass(frozen=True) class Call: call_id: str; half: int; lock_t: float; player_id: str; team: str; answer: str` (`call_id` = `"c-" + id of the receiver's first on-ball event`).
  - `find_moments(match: Match) -> list[Call]` — every decision moment in the match, sorted by `(half, lock_t)`.
  - `select_calls(moments: list[Call], half: int, start_t: float, end_t: float) -> list[Call]` — the calls for one play range, sorted by `lock_t`.
  - Constants: `ON_BALL = {"pass", "carry", "shot"}`, `MIN_PASS_END_X = 60.0`, `DRIBBLE_CARRY_M = 8.0`, `MIN_GAP_S = 5.0`, `CALL_WINDOW_S = 5.0`.

Decision moment rule (pin exactly; the data has no dribble events, only carries):
1. A `pass` with `outcome == "complete"` and `end[0] >= MIN_PASS_END_X` (acting-team frame).
2. The receiver is the player of the next `ON_BALL` event; skip if that event is the other team's or the passer's own.
3. The receiver's sequence = consecutive `ON_BALL` events by that player.
4. Answer: any `shot` → `"SHOOT"`; else total carry distance (Euclidean `start`→`end`) `>= DRIBBLE_CARRY_M` → `"DRIBBLE"`; else any `pass` → `"PASS"`; else not a moment.
5. `lock_t` = time of the sequence's first event (the ball moves).

Selection rule for one play range: candidates are moments with `half` equal and `start_t <= lock_t <= end_t`. Greedy by priority `SHOOT` > `DRIBBLE` > `PASS`, then earlier `lock_t`; accept a candidate only if its `lock_t` is at least `MIN_GAP_S` from every accepted call. Return accepted, sorted by `lock_t`.

- [ ] **Step 1: Write the failing tests** (values checked against the data on 2026-10-27)

```python
from backend.ingest.loader import load_match
from backend.demo.calls import find_moments, select_calls

M = load_match("nbr-cal-seed42")
MOMENTS = find_moments(M)

def test_moment_mix():
    answers = [c.answer for c in MOMENTS]
    assert len(MOMENTS) == 247
    assert answers.count("SHOOT") == 24 and answers.count("DRIBBLE") == 32

def test_saka_goal_call_is_shoot():
    calls = select_calls(MOMENTS, 1, 1400, 1440)
    assert [round(c.lock_t) for c in calls] == [1422, 1432]
    assert calls[-1].player_id == "910109" and calls[-1].answer == "SHOOT"

def test_min_gap_respected():
    calls = select_calls(MOMENTS, 2, 3595, 3620)
    assert [round(c.lock_t) for c in calls] == [3608, 3613]
    assert all(b.lock_t - a.lock_t >= 5.0 for a, b in zip(calls, calls[1:]))

def test_empty_range():
    assert select_calls(MOMENTS, 1, 0, 8) == []
```

- [ ] **Step 2: Run to verify failure** — `.venv/bin/python -m pytest backend/tests/test_demo_calls.py -q` → FAIL.
- [ ] **Step 3: Implement `find_moments` and `select_calls`** per the rules.
- [ ] **Step 4: Run** → PASS. If a count differs by a few, print the moments and check the rule was followed exactly before touching the expected values.
- [ ] **Step 5: Commit** — `[backend] Add demo decision moments: PASS/SHOOT/DRIBBLE from synthetic events`.

---

### Task 3: Points

**Files:**
- Create: `backend/demo/points.py`
- Test: `backend/tests/test_demo_points.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class CallScore: too_quick: bool; correct: bool; base: int; correct_bonus: int; streak_bonus: int; points: int; streak: int`
  - `score_call(pick: str | None, answer: str, streak: int) -> CallScore` — `streak` is the streak before this call; returned `streak` is after it.

- [ ] **Step 1: Write the failing tests**

```python
from backend.demo.points import score_call

def test_too_quick_keeps_streak():
    s = score_call(None, "PASS", 2)
    assert s.too_quick and s.points == 0 and s.streak == 2

def test_wrong_pays_ten_resets_streak():
    s = score_call("SHOOT", "PASS", 4)
    assert (s.correct, s.points, s.streak) == (False, 10, 0)

def test_correct_without_streak_bonus():
    s = score_call("PASS", "PASS", 1)
    assert (s.points, s.streak, s.streak_bonus) == (110, 2, 0)

def test_streak_bonus_steps():
    assert score_call("PASS", "PASS", 2).streak_bonus == 50   # 3rd in a row
    assert score_call("PASS", "PASS", 3).points == 210        # 4th: 10 + 100 + 100
```

- [ ] **Step 2: Run to verify failure** → FAIL.
- [ ] **Step 3: Implement `score_call`** with the Global Constraints values.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** — `[backend] Add demo points: design table with streak bonus and too-quick`.

---

### Task 4: Highlights timeline

**Files:**
- Create: `backend/demo/script.py`
- Test: `backend/tests/test_demo_script.py`

**Interfaces:**
- Consumes: Task 1 (`TEAMS`, `display_name`, `fixed_xy`, `clock_label`), Task 2 (`find_moments`, `select_calls`, `Call`, `CALL_WINDOW_S`).
- Produces:
  - `@dataclass(frozen=True) class Play: half: int; start_t: float; end_t: float; title: str`
  - `@dataclass(frozen=True) class Pause: kind: str; seconds: float` (`kind` ∈ `"half_time"`, `"full_time"`)
  - `SCRIPT: tuple[Play | Pause, ...]` — exact values:
    ```
    Play(1, 0, 8, "Kickoff"), Play(1, 88, 115, "Opening minutes"),
    Play(1, 1400, 1440, "Mensah's goal"), Play(1, 2848, 2857, "Half time"),
    Pause("half_time", 25), Play(2, 3595, 3620, "Calder hit back"),
    Play(2, 4775, 4797, "The winner"), Play(2, 5655, 5668, "Full time"),
    Pause("full_time", 0)
    ```
  - `JUMP_CARD_S = 2.0` (real seconds a jump card shows before the next Play).
  - `@dataclass(frozen=True) class Cue: at: float; type: str; data: dict` (`at` = real seconds from demo start).
  - `@dataclass(frozen=True) class Timeline: cues: tuple[Cue, ...]; calls: dict[str, Call]; duration: float`
  - `build_timeline(match: Match, script: tuple = SCRIPT) -> Timeline`, cues sorted by `at` (stable).

Cue types and `data` (this is the wire format the frontend reads; the session sends every cue except `call.lock` verbatim as `{"type": cue.type, "data": cue.data}`):

| type | when | data |
|---|---|---|
| `demo.jump` | before every Play except the first; lasts `JUMP_CARD_S` | `{title, clock}` (clock = label at the Play's start) |
| `demo.clock` | each whole real second inside a Play, starting at the Play's start | `{half, clock, score: [home, away]}` (goals with `(half, t) <=` this tick) |
| `demo.event` | each event with `type in ON_BALL ∪ {"goal", "save", "kickoff"}` inside the Play, at `play_start_real + (t - start_t)` | `{type, team: TEAMS code, player: display_name, xy, end_xy}` (fixed frame) |
| `demo.goal` | at a `goal` event | `{team: code, player, clock, score: [home, away]}` (score after the goal) |
| `call.open` | `lock_real - window`, window = `min(CALL_WINDOW_S, lock_t - previous accepted lock_t in this Play)` | `{call_id, player, team: code, prompt, options: ["PASS","SHOOT","DRIBBLE"]}`; `prompt` = `f"{player} on the ball. What's the call?"` |
| `call.lock` | at the call's `lock_t` | `{call_id}` (session turns it into `call.locked` + `call.result`) |
| `demo.half_time` / `demo.full_time` | at a Pause | `{score: [home, away], seconds}` |

`teams` for the session message: `{"home": TEAMS["NBR"], "away": TEAMS["CAL"]}`.

- [ ] **Step 1: Write the failing tests**

```python
from backend.ingest.loader import load_match
from backend.demo.script import build_timeline

TL = build_timeline(load_match("nbr-cal-seed42"))
types = [c.type for c in TL.cues]

def test_about_three_minutes():
    assert 165 <= TL.duration <= 195

def test_calls_and_mix():
    assert len(TL.calls) == 7
    answers = sorted(c.answer for c in TL.calls.values())
    assert answers.count("SHOOT") == 3 and answers.count("PASS") == 2 and answers.count("DRIBBLE") == 2

def test_goals_and_final_score():
    goals = [c.data for c in TL.cues if c.type == "demo.goal"]
    assert [g["player"] for g in goals] == ["Mensah", "Doyle", "Hale"]
    assert goals[-1]["score"] == [2, 1]
    assert [c for c in TL.cues if c.type == "demo.full_time"][0].data["score"] == [2, 1]

def test_every_call_opens_before_it_locks():
    opens = {c.data["call_id"]: c.at for c in TL.cues if c.type == "call.open"}
    locks = {c.data["call_id"]: c.at for c in TL.cues if c.type == "call.lock"}
    assert opens.keys() == locks.keys() == TL.calls.keys()
    assert all(3.0 <= locks[k] - opens[k] <= 5.0 for k in opens)

def test_cues_sorted_and_jumps():
    assert [c.at for c in TL.cues] == sorted(c.at for c in TL.cues)
    assert types.count("demo.jump") == 6 and types[-1] == "demo.full_time"
```

- [ ] **Step 2: Run to verify failure** → FAIL.
- [ ] **Step 3: Implement `build_timeline`.** Walk `script` keeping a real-time cursor: a Play adds `JUMP_CARD_S` (if not first) then `end_t - start_t`; a Pause adds `seconds`. `duration` = final cursor.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** — `[backend] Add demo highlights timeline: 3-minute cut with calls, goals and jumps`.

---

### Task 5: Demo session and WebSocket

**Files:**
- Create: `backend/demo/session.py`, `backend/demo/router.py`
- Modify: `backend/app/main.py` (inside `create_app`, after the existing routes: `app.include_router(make_demo_router(sleep=sleep))`)
- Test: `backend/tests/test_demo_session.py`

**Interfaces:**
- Consumes: Task 3 (`score_call`), Task 4 (`Timeline`, `build_timeline`, `Cue`).
- Produces:
  - `class DemoSession:` `__init__(self, timeline: Timeline, team: str)`; `async run(self, send: Callable[[dict], Awaitable[None]], sleep: Callable[[float], Awaitable[None]]) -> None`; `answer(self, call_id: str, pick: str) -> bool` (True if accepted); attributes `total: int`, `streak: int`, `open_call: str | None`.
  - `make_demo_router(*, sleep=asyncio.sleep, load=lambda: load_match("nbr-cal-seed42"), max_sessions: int | None = None) -> APIRouter` — builds the timeline once (lazily, first connect) and serves `WS /demo/ws`. `max_sessions` default from env `MAX_DEMO_SESSIONS` (50).

Wire protocol on `/demo/ws` (envelope `{"type", "data"}`):
- Client → server first message: `{"type": "start", "data": {"team": "NBR" | "CAL"}}` within 10 s, else close 1008. Then any number of `{"type": "call.answer", "data": {"call_id", "pick"}}`.
- Server → client: first `{"type": "demo.session", "data": {"teams": …, "you": {"team": code}, "duration": float}}`, then every cue per Task 4, with `call.lock` replaced by `{"type": "call.locked", "data": {"call_id"}}` followed by `{"type": "call.result", "data": {"call_id", "answer", "pick", "too_quick", "correct", "points", "streak_bonus", "total", "streak"}}`. After `demo.full_time` the server closes with 1000.

Session rules: `run` sleeps `cue.at - elapsed` (elapsed = sum of sleeps, not wall clock) before each cue. `call.open` sets `open_call`; `answer` accepts only `call_id == open_call`, only the first time, only picks in `{"PASS","SHOOT","DRIBBLE"}`; `call.lock` clears `open_call` and scores with `score_call`. Router: receive loop runs beside the `run` task; on disconnect or `run` finishing, cancel the other and release the session slot.

- [ ] **Step 1: Write the failing tests** (TestClient websocket; inject `sleep` that returns immediately; `TestClient(create_app(sleep=fast_sleep))`)

```python
def test_plays_to_full_time_with_results(client):  # all calls answered "SHOOT"
    msgs = play(client, team="NBR", pick="SHOOT")
    assert msgs[0]["type"] == "demo.session" and msgs[-1]["type"] == "demo.full_time"
    results = [m["data"] for m in msgs if m["type"] == "call.result"]
    assert len(results) == 7 and sum(r["correct"] for r in results) == 3
    # answers in order: D, D, P, S, P, S, S → correct at calls 4, 6, 7; streak peaks at 2, so no bonus
    assert results[-1]["total"] == 7 * 10 + 3 * 100 == 370

def test_no_answers_reaches_full_time(client):
    msgs = play(client, team="CAL", pick=None)
    results = [m["data"] for m in msgs if m["type"] == "call.result"]
    assert all(r["too_quick"] for r in results) and results[-1]["total"] == 0

def test_double_answer_counts_once(): ...   # DemoSession directly: answer twice → second returns False
def test_answer_after_lock_ignored(): ...   # answer after call.lock → False, total unchanged
def test_bad_messages(client): ...          # non-JSON and unknown pick never crash; start missing → close 1008
def test_session_cap(): ...                 # make_demo_router(max_sessions=1): 2nd socket closed 1013
def test_disconnect_cancels_session(): ...  # close mid-run → router's active count back to 0
```

`play()` helper: connect `/demo/ws`, send `start`, read until close; on each `call.open` send `call.answer` with `pick` unless `pick is None`. With a no-op `sleep`, answers race the lock, so the helper must make `sleep` yield (`await asyncio.sleep(0)`) and tests that check correctness drive `DemoSession` directly where ordering matters.

- [ ] **Step 2: Run to verify failure** → FAIL.
- [ ] **Step 3: Implement `DemoSession`, `make_demo_router`, and the one-line include in `create_app`.**
- [ ] **Step 4: Run the whole suite** — `.venv/bin/python -m pytest -q` → all PASS (existing tests untouched).
- [ ] **Step 5: Commit** — `[backend] Add demo session: /demo/ws plays the highlights cut and scores calls`.

---

### Task 6: Fan app frontend, served at `/`

**Files:**
- Create: `frontend/fan-app/index.html`, `frontend/fan-app/styles.css`, `frontend/fan-app/app.js`, `frontend/fan-app/assets/crests/nbr.svg`, `frontend/fan-app/assets/crests/cal.svg` (copied from `origin/frontend/demo-ux:frontend/demo-ux/assets/crests/`), `frontend/fan-app/README.md`
- Modify: `backend/app/main.py` (end of `create_app`: if `REPO_ROOT / "frontend" / "fan-app"` exists, `app.mount("/", StaticFiles(directory=..., html=True), name="fan-app")` — after all routes so `/api/*`, `/ws`, `/demo/ws` still match first); `infra/Dockerfile` (`COPY frontend/fan-app/ frontend/fan-app/`)
- Test: `backend/tests/test_demo_static.py`

**Interfaces:**
- Consumes: Task 5 wire protocol. WebSocket URL: `(location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/demo/ws"`.

Screens (one page, show/hide sections; 390 × 844 first, readable at any phone width):
1. **Landing** — from `Main.dc.html`: live match card "Northbridge vs Calder", "Play this match". Other match cards not shown.
2. **Pick team** — from `Pick.dc.html`, team choice only (Northbridge / Calder crests). Player backing is Step 5; leave it out.
3. **Live** — from `Live.dc.html` "Real time" tab: scoreboard (crests, score, `clock`), 2D pitch (SVG 105 × 68, ball animates from `xy` to `end_xy` over the gap to the next event; acting player's name label near the ball), call card with the three option buttons and `prompt`, points pop-ups and running total from `HUD.dc.html` (+10 / +100 / streak), "Too quick!" on `too_quick`, goal banner on `demo.goal`, jump card on `demo.jump` (`title` + `clock`, `JUMP_CARD_S` long). No Forecast tab yet.
4. **Half time / Full time** — minimal cards for now: score and total, "Play again" on full time (reconnects from kickoff). Step 3 replaces them.

- [ ] **Step 1: Write the failing test**

```python
def test_root_serves_fan_app(client):
    r = client.get("/")
    assert r.status_code == 200 and "Matchday Call" in r.text

def test_api_still_wins_over_static(client):
    assert client.get("/api/matches").status_code == 200
```

- [ ] **Step 2: Run to verify failure** → FAIL (404 at `/`).
- [ ] **Step 3: Build the three files, copy the crests, add the mount and the Dockerfile line.**
- [ ] **Step 4: Run** `.venv/bin/python -m pytest -q` → PASS. Then run `uvicorn backend.app.main:app --port 8000`, open `http://localhost:8000` in Chrome at 390 × 844, and play to full time: calls appear and lock, points pop, Mensah's goal shows 1–0, jumps show, final 2–1. Record a GIF named `fan_app_live_calls.gif`.
- [ ] **Step 5: Commit** — `[frontend] Add fan app: landing, team pick and live calls against /demo/ws`.

---

### Task 7: Redeploy and verify

**Files:** none (commands only; `infra/README.md` has them).

- [ ] **Step 1: Build and push** `matchdaycall46e7fe.azurecr.io/matchday-call:v2` (linux/amd64), then `az containerapp update -g rg-matchday-call -n matchday-call --image …:v2`.
- [ ] **Step 2: Verify** — `GET /` returns the app; `/readyz` 200; a scripted `/demo/ws` client (scratchpad `ws_check.py` adapted to send `start`) receives `demo.session` … `demo.full_time` in about 3 minutes; open the URL on a real phone and play once.
- [ ] **Step 3: Push the branch and open a PR** to `main` with the checklist from `CLAUDE.md`; merge on the user's approval.
