# Fan App — 1.5-Minute Cut with Real Dribbles Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Shorten the demo to ~82 s (the demo video is 2 minutes) and make calls follow the team's agreed rule: a call opens when the ball is passed to a player and locks when he passes, shoots or dribbles (real `Dribble` events; a carry does not lock).

**Architecture:** `backend/demo/calls.py` switches its source from the ingested `Match` (which drops `Dribble` events and pass recipients) to the raw StatsBomb file `data/source/events/<hudl id>.json`, read directly (ingest stays untouched). `Call` gains `open_t`. `backend/demo/script.py` gets the new 4-segment `SCRIPT` and opens each call at its `open_t`. Everything downstream (session, forecasts, shapes, frontend) keeps its interfaces; demo tests whose expected values depend on the old cut are updated to the new cut.

**Tech Stack:** Python 3.11, pytest.

**Spec:** `docs/superpowers/specs/2026-10-27-fan-app-demo-design.md`, amended by the team doc "粉丝 App 演示：后端改动说明" (decisions confirmed by Steve on 10/27) and the user's choices on 2026-10-27: "shorten to 1.5 min, use real dribbles". Team-doc rules used here: calls open when the ball is passed to the player and lock when he passes / shoots / dribbles; a carry never locks; only moments with ≥ 3 s to answer; answers balanced so always-PASS can't win; ≥ 6 s between calls so the result card has time; script of four ~15 s segments + a ~10 s half time, no kickoff segment.

## Global Constraints

- Raw time: `t = minute*60 + second + fractional part of timestamp` (same clock as `MatchEvent.t`; second half continues from 45:00 = 2700). Raw pitch is 120 × 80; the demo already uses ingested events (105 × 68) for positions — calls only need times, players and types.
- Decision moment (exact): a raw `Pass` with no `pass.outcome` and a `pass.recipient` R. Walk the following raw events of type `Pass`, `Carry`, `Shot`, `Dribble` in the same period (skip `Ball Receipt*`); stop if one is by a player other than R; R's first `Pass` → `PASS`, `Shot` → `SHOOT`, `Dribble` → `DRIBBLE` (any dribble outcome) ends the walk and makes the moment. `open_t` = the pass's t, `lock_t` = that action's t. Keep only `lock_t - open_t >= 3.0` (`MIN_WINDOW_S`).
- `Call` fields: `call_id: str` (`"c-" + raw id of the locking event`), `half: int`, `open_t: float`, `lock_t: float`, `player_id: str`, `team: str` (`"NBR"`/`"CAL"`, mapped from the raw team id the same way ingest does — reuse ingest's team-code lookup read-only), `answer: str`.
- Selection inside a Play `[start_t, end_t]`: candidates with `start_t <= open_t` and `lock_t <= end_t`; greedy by priority SHOOT > DRIBBLE > PASS, then earlier `lock_t`; accept if, against every accepted call, `abs(open_t - other.open_t) >= MIN_GAP_S` (6.0) and the two windows do not overlap (`open_t >= other.lock_t or other.open_t >= lock_t`). Return sorted by `open_t`.
- New `SCRIPT` (exact):
  ```
  Play(1, 694, 716, "Arsenal on the attack"),
  Play(1, 1422, 1437, "Saka's goal"),
  Pause("half_time", 10),
  Play(2, 3600, 3616, "Chelsea hit back"),
  Play(2, 4780, 4793, "The winner"),
  Pause("full_time", 0),
  ```
  Expected: duration 82.0 s; 3 jumps; calls in order (open_t ≈, answer, player): 696.6 PASS 910104, 703.4 DRIBBLE 910106, 711.7 SHOOT 910109 (blocked), 1430.0 SHOOT 910109 (Saka goal), 3606.0 PASS 910211, 4786.2 SHOOT 910105 (Saliba goal). Goals Saka, João Pedro, Saliba; final 2–1.
- `call.open` cue at `play_real_start + (open_t - start_t)`; `call.lock` at `... + (lock_t - start_t)`; window 3–8 s. Everything else in the timeline (clock, events, shapes, goals, jumps, half/full time, forecasts settlement, `t` in clock) unchanged in behaviour.
- Do not modify `backend/ingest/`, `backend/app/replay.py`, `backend/bank/`, `backend/resolve/`, `backend/scoring/`, or pre-existing (non-demo) tests. Demo tests (`backend/tests/test_demo_*.py`) may be updated where their expected values encode the old cut; keep every behavioural assertion, only change cut-specific numbers/clock labels.
- Commits `[area] Verb thing: description` + blank line + `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Branch `backend/demo-live-calls`. Tests `.venv/bin/python -m pytest -q`.

## Review Focus

1. **Always-one-answer strategy** — always SHOOT scores 6×10 + 3×100 = 360; always PASS 6×10 + 2×100 = 260; perfect play beats both (with streak bonus). Test in Task 2.
2. **Overlapping windows** — no call opens before the previous call locked. Test `test_windows_do_not_overlap` (Task 2).
3. **Forecast behaviour on the new cut** — windows still settle after each jump and at half/full time; final locks at the first half-2 tick. Existing forecast tests updated, not weakened (Task 2).
4. **Frontend copy** — the jump card and HT card still read well with a 10 s half time ("Second half starts in 10 seconds"); the HT pick button still usable (Task 2 report; controller checks in Chrome).

---

### Task 1: Calls from raw events

**Files:** Modify `backend/demo/calls.py` (replace the moment source and selection rule; keep the module name); Test `backend/tests/test_demo_calls.py` (rewrite for the new rule).

**Interfaces (Produces):** `Call` (fields above); `MIN_WINDOW_S = 3.0`; `MIN_GAP_S = 6.0`; `find_moments(match_id: str = "nbr-cal-seed42") -> list[Call]` sorted by `(half, open_t)`; `select_calls(moments, half, start_t, end_t) -> list[Call]`; keep `OPTIONS`/`ON_BALL` only if still used elsewhere (grep `backend/demo`).

- [ ] **Step 1: Failing tests**

```python
from backend.demo.calls import find_moments, select_calls

MOMENTS = find_moments()

def test_only_answerable_moments():
    assert MOMENTS and all(c.lock_t - c.open_t >= 3.0 for c in MOMENTS)
    answers = {c.answer for c in MOMENTS}
    assert answers == {"PASS", "SHOOT", "DRIBBLE"}

def test_saka_goal_is_a_shoot_call():
    calls = select_calls(MOMENTS, 1, 1422, 1437)
    assert [(round(c.open_t, 1), c.answer, c.player_id) for c in calls] == [(1430.0, "SHOOT", "910109")]
    assert round(calls[0].lock_t, 1) == 1434.3

def test_first_segment_has_all_three():
    calls = select_calls(MOMENTS, 1, 694, 716)
    assert [c.answer for c in calls] == ["PASS", "DRIBBLE", "SHOOT"]

def test_gap_and_no_overlap():
    calls = select_calls(MOMENTS, 1, 694, 716)
    for a, b in zip(calls, calls[1:]):
        assert b.open_t - a.open_t >= 6.0 and b.open_t >= a.lock_t

def test_winner_and_equaliser_segments():
    assert [(c.answer, c.player_id) for c in select_calls(MOMENTS, 2, 4780, 4793)] == [("SHOOT", "910105")]
    assert [(c.answer, c.player_id) for c in select_calls(MOMENTS, 2, 3600, 3616)] == [("PASS", "910211")]

def test_carry_never_locks():
    # every lock is a pass, shot or dribble in the raw data (a carry is skipped as part of the walk)
    assert all(c.call_id.startswith("c-") for c in MOMENTS)
```

- [ ] **Step 2:** FAIL. **Step 3:** implement (raw file path: resolve the hudl id via `backend.ingest.statsbomb` helpers read-only, as `backend/demo/shape.py` already does). **Step 4:** the calls tests pass; other demo tests may now fail because `script.py` still uses the old fields — that's Task 2; do not touch them here, but report which fail. **Step 5:** commit `[backend] Fix demo calls: open on the pass, lock on pass/shot/real dribble`.

### Task 2: The 82-second cut

**Files:** Modify `backend/demo/script.py` (new `SCRIPT`, open calls at `open_t`); update cut-specific expectations in `backend/tests/test_demo_script.py`, `test_demo_session.py`, `test_demo_shape.py`, `test_demo_forecast_session.py`; frontend copy only if a fixed "25 seconds"/"3-MIN" string exists in `frontend/fan-app/` (make it read the server's `seconds`, and "3-MIN HIGHLIGHTS" → "90-SECOND HIGHLIGHTS").

- [ ] **Step 1:** Update the tests to the new cut first (RED): duration 82.0; 6 calls with answers `["PASS","DRIBBLE","SHOOT","SHOOT","PASS","SHOOT"]`; 3 jumps; goals Saka, João Pedro, Saliba, final [2, 1]; always-SHOOT total 360 and always-PASS total 260 (session); new `test_windows_do_not_overlap` (every call.open `at` ≥ previous call.lock `at`); forecast tests re-anchored to the new Plays (e.g. pick at the first tick of "Arsenal on the attack", settle after the jump before the first tick of "Saka's goal"; half-time settlement at `demo.half_time`; final locks before the first half-2 tick) with the same assertions on behaviour.
- [ ] **Step 2:** implement. **Step 3:** full suite green, run demo test files 3×. **Step 4:** commit `[backend] Add demo 90-second cut: four segments, 10 s half time`.

### Task 3: Redeploy

- [ ] Build/push `matchday-call:v5`, update the app, scripted live run (duration ≈ 82 s, 6 calls, forecasts settle, final_result), push branch, PR comment.
