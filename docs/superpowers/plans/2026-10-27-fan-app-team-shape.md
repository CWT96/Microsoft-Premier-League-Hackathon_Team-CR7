# Fan App — All 22 Players on the Pitch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** The live pitch shows all 22 players (11 Arsenal, 11 Chelsea), each at their formation spot, with both teams sliding as a block toward the ball, and the player on the ball exactly where the data puts him.

**Architecture:** The dataset has no tracking, only the on-ball player's location per event. A new pure module `backend/demo/shape.py` estimates every on-pitch player's position from (a) their named lineup position (e.g. "Left Back"), (b) the ball's position. `build_timeline` adds a `demo.shape` cue next to every `demo.event` and at each Play's start. The frontend draws 22 shirt-number dots and animates them with CSS transitions.

**Tech Stack:** Python 3.11, pytest; vanilla JS + SVG.

**Spec:** `docs/superpowers/specs/2026-10-27-fan-app-demo-design.md` (step 2), plus the user's request on 2026-10-27: "there should be all [players] … for both my team and other team in the field". The user accepted that off-ball positions are estimates (no tracking data).

## Global Constraints

- Match `nbr-cal-seed42`; NBR = home = Arsenal (`ARS`), CAL = away = Chelsea (`CHE`). Fixed pitch frame 105 × 68: Arsenal attacks left → right all match; away coordinates convert with `(105 - x, 68 - y)` (use `names.fixed_xy`).
- Do not change: call selection, points, the 7 calls, duration (181 s), existing cue types or their data, `/demo/ws` protocol other than the new additive cue `demo.shape`. All existing tests stay green; do not edit existing tests except adding new ones.
- Do not modify `backend/app/replay.py`, `backend/bank/`, `backend/resolve/`, `backend/scoring/`, `backend/ingest/`.
- Commits: `[area] Verb thing: description` + blank line + `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Branch `backend/demo-live-calls`.
- Tests: `.venv/bin/python -m pytest -q`.

## Review Focus

1. **Substitutions** — after a sub, the incoming player takes the pitch and the outgoing one disappears; always exactly 11 per team (no red cards in this match — if any are found, report). Test: `test_substitute_replaces_player` (Task 1).
2. **Players off the pitch edge** — every x in [1, 104], y in [1, 67]. Test: `test_all_inside_pitch` (Task 1).
3. **Wire size** — 22 players × ~45 shape cues must stay small (< 60 KB total for the timeline JSON). Test: `test_shape_payload_small` (Task 1).
4. **Frontend never gets stuck if `demo.shape` is missing or has fewer players** — dots not in the latest cue are hidden. (Task 2 review.)

---

### Task 1: Team shape in the timeline

**Files:**
- Create: `backend/demo/shape.py`
- Modify: `backend/demo/script.py` (add `demo.shape` cues; add `"demo.shape"` to `_RANK` right after `demo.event`)
- Test: `backend/tests/test_demo_shape.py`

**Interfaces:**
- Produces:
  - `SLOTS: dict[str, tuple[float, float]]` — lineup position name → base (x, y) for a team attacking left → right (small y = that team's left touchline, StatsBomb convention). Exact values:
    ```
    Goalkeeper (5, 34)
    Left Back (24, 10)          Right Back (24, 58)
    Left Center Back (17, 25)   Center Back (16, 34)   Right Center Back (17, 43)
    Left Wing Back (32, 8)      Right Wing Back (32, 60)
    Left Defensive Midfield (32, 26)  Center Defensive Midfield (32, 34)  Right Defensive Midfield (32, 42)
    Left Center Midfield (40, 22)     Right Center Midfield (40, 46)
    Left Midfield (44, 10)            Right Midfield (44, 58)
    Center Attacking Midfield (50, 34)
    Left Wing (58, 10)                Right Wing (58, 58)
    Left Center Forward (62, 28)  Center Forward (64, 34)  Right Center Forward (62, 40)
    ```
    Unknown names fall back by group: GK (5,34), DF (20,34), MF (40,34), FW (62,34).
  - `@dataclass(frozen=True) class Slot: player_id: str; team: str; position: str; half_from: int; t_from: float; half_to: int | None; t_to: float | None`
  - `load_slots(match_id: str = "nbr-cal-seed42") -> list[Slot]` — reads `data/source/lineups/<hudl id>.json` (resolve the hudl id the same way `backend/ingest/statsbomb.read_match` does; reuse its helpers if importable without modifying ingest). Each `positions` entry becomes a Slot; `from`/`to` are `"MM:SS"` match-clock strings with `from_period`/`to_period` → convert to (half, t seconds) using the same clock as `MatchEvent.t` (check against a known substitution event: NBR player 910107 goes off at half 2, t ≈ 3409.96).
  - `on_pitch(slots: list[Slot], half: int, t: float) -> list[Slot]` — slots active at (half, t) (from ≤ now < to; `to=None` = until the end).
  - `team_shape(slots_now: list[Slot], ball: tuple[float, float], holder_id: str | None) -> list[dict]` — returns `[{"id", "team": code, "num", "x", "y"}]` in the fixed frame, rounded to 1 decimal. `num` = `names.PLAYERS[id][1]` if mapped, else the lineup jersey number (keep it in Slot or look it up; your choice). Algorithm (ball given in fixed frame):
    ```
    for each player: convert ball to the player's own attacking frame (home: as is; away: (105-x, 68-y))
      shift_x = (ball_own_x - 52.5) * 0.45
      shift_y = (ball_own_y - 34) * 0.25
      base = SLOTS[position]
      if GK: x = 5 + max(0, shift_x) * 0.15; y = 34 + (ball_own_y - 34) * 0.1
      else:  x = base_x + shift_x; y = 34 + (base_y - 34) * 0.9 + shift_y
      clamp x to [1, 104], y to [1, 67]; convert back to the fixed frame (away mirrored)
    the holder (if on pitch) is placed exactly at the ball.
    ```
- Consumes: `names.TEAMS`, `names.PLAYERS`, `names.fixed_xy`; `script.py` internals (`_play_cues`).

Timeline additions (in `script.py`):
- At each Play's start (same `at` as its first `demo.clock`): one `demo.shape` with ball = xy of the first ball event (pass/carry/shot/kickoff with xy) inside the Play, else (52.5, 34), holder = None.
- With every `demo.event` that has an `xy`: one `demo.shape` at the same `at`, ball = that event's fixed `xy`, holder = its player_id.
- `demo.shape` data: `{"players": [...22 dicts...]}`.

- [ ] **Step 1: Write the failing tests** (`backend/tests/test_demo_shape.py`)

```python
from backend.ingest.loader import load_match
from backend.demo.script import build_timeline
from backend.demo.shape import load_slots, on_pitch, team_shape
import json

TL = build_timeline(load_match("nbr-cal-seed42"))
SHAPES = [c for c in TL.cues if c.type == "demo.shape"]

def test_every_shape_has_eleven_a_side():
    assert SHAPES
    for c in SHAPES:
        teams = [p["team"] for p in c.data["players"]]
        assert teams.count("ARS") == 11 and teams.count("CHE") == 11

def test_all_inside_pitch():
    for c in SHAPES:
        assert all(1 <= p["x"] <= 104 and 1 <= p["y"] <= 67 for p in c.data["players"])

def test_holder_is_on_the_ball():
    events = [c for c in TL.cues if c.type == "demo.event" and c.data["xy"]]
    shapes = {c.at: c for c in SHAPES}
    saka = next(e for e in events if e.data["player"] == "Saka" and e.data["type"] == "shot")
    dot = next(p for p in shapes[saka.at].data["players"] if p["num"] == 7 and p["team"] == "ARS")
    assert [dot["x"], dot["y"]] == saka.data["xy"]

def test_goalkeepers_stay_home():
    for c in SHAPES:
        gks = {p["team"]: p for p in c.data["players"] if p["num"] == 1}
        assert gks["ARS"]["x"] < 20 and gks["CHE"]["x"] > 85

def test_substitute_replaces_player():
    slots = load_slots()
    before = {s.player_id for s in on_pitch(slots, 2, 3400.0)}
    after = {s.player_id for s in on_pitch(slots, 2, 3420.0)}
    assert "910107" in before and "910107" not in after
    assert len(after) == 22

def test_calls_and_duration_unchanged():
    assert len(TL.calls) == 7 and TL.duration == 181.0 and TL.cues[-1].type == "demo.full_time"

def test_shape_payload_small():
    size = len(json.dumps([c.data for c in TL.cues]))
    assert size < 60_000
```

- [ ] **Step 2: Run** `.venv/bin/python -m pytest backend/tests/test_demo_shape.py -q` → FAIL (ImportError).
- [ ] **Step 3: Implement `shape.py` and the timeline additions.** If `test_shape_payload_small` fails, shorten the per-player dict keys only if needed (report it); do not drop shapes.
- [ ] **Step 4: Run** full suite → PASS (existing demo tests must still pass unchanged).
- [ ] **Step 5: Commit** — `[backend] Add demo team shape: all 22 players placed from formation and ball`.

---

### Task 2: Draw 22 players on the pitch

**Files:**
- Modify: `frontend/fan-app/app.js`, `frontend/fan-app/styles.css`, `frontend/fan-app/index.html` (only if a layer is needed in the SVG)

**Interfaces:**
- Consumes: `demo.shape {players: [{id, team: "ARS"|"CHE", num, x, y}]}` in the same 105 × 68 SVG frame the ball uses; arrives at the same moment as the matching `demo.event` (after it).

Behaviour:
- One SVG group per player id (created on first sight, reused): a circle r ≈ 1.6 with the shirt number centred (font ~1.6 units, bold). Arsenal: red fill `#EF0107`, white number; Chelsea: blue fill `#034694`, white number. Thin dark outline so dots read on the green pitch.
- Position updates move the group with a CSS transform transition (~0.8 s ease-out, same feel as the ball glide). Players not in the latest `demo.shape` are hidden (handles substitutions).
- Layer order: pitch lines < players < ball < name label. The existing ball, glide and name label behaviour stays as is.
- Reset all player dots on "Play again" (new socket) — no dots from the previous run.
- Respect the reviewed patterns: messages from stale sockets ignored; no `innerHTML` with server data.

- [ ] **Step 1:** Implement. **Step 2:** `.venv/bin/python -m pytest -q` green (static tests). Headless smoke: page loads, no JS errors in a full run if you can drive one. **Step 3: Commit** — `[frontend] Add fan app players: 22 shirt-number dots follow the team shape`.

---

### Task 3: Redeploy

- [ ] Build/push `matchday-call:v3`, `az containerapp update`, verify `/` and a scripted `/demo/ws` run (counts `demo.shape` cues), then push the branch (PR #9 updates).
