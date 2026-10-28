# frontend/director/ — Director view

**Owner:** `tianawangty` (merge reviewed by `CWT96`). The backstage page for the demo: when the timing agent opens, holds or blocks a moment, when The Premier X opens its calls, and how intense play is, all on one match clock. Served at `/director/` by the FastAPI app.

## Run

```bash
uvicorn backend.app.main:app --port 8000     # then open http://localhost:8000/director/
```

It must be served by the app (not opened as a file): the phone on the right is the real The Premier X at `/` in an iframe, and the page reads its `window.app.state` (same origin).

## What is on the page

The layout of the brainstorm draft: controls on top, the pitch and the chart on the left, the phone and the log on the right.

- **Controls:** ▶ Play starts the match on the phone (its START PLAYING action; without the phone it replays the chart at 4 match seconds per real second), the clock (half, time, score, `mode`) and a slider over the whole match. Under the slider: `mode: attack` (orange) and The Premier X clips (purple) across the match.
- **Pitch:** mirrors the phone's live pitch every frame (`window.Pitch` in `frontend/premier-x/pitch.js`: 22 players, ball and holder ring, in metres). Idle until a match starts on the phone.
- **Chart (±60 s around the cursor):** `dull_score`, `threat`, `cost` with `CANDIDATE_COST` dashed; `mode: attack` bands; `Moment` windows (`open_at` → `lock_at`); `timing.decision` markers (▲ open, ◆ hold, ✕ blocked, ■ scheduled, · calm / silent; hover for the reason); the fan's team's clips and calls (the call on the phone is outlined).
- **Phone:** the real The Premier X. While it plays a clip, the cursor follows its `demo.clock` (`half`, `t`) and the chart shows the team the fan picked.
- **Log:** every `timing.decision` in the window up to the cursor: action, source, situation, tone and reason.

## Data

`data.js` (`window.TRACE`) is generated and committed so the image needs no build step:

```bash
python frontend/director/build_trace.py    # from the repo root; deterministic
```

It runs the default match through `Detector.run` → `TimingAgent(RulePolicy()).step` (the same loop as `backend/agents/timing_sim.py`, and the policy `replay.py` uses by default) and adds each team's highlight clips (`SCRIPTS`) and the calls `build_timeline` picks (LLM cache only, no network). Rerun it whenever the detector, timing rules, match data or demo script change.

The dataset has no tracking, so `movement` and `pressure_m` are not shown. The Premier X calls come from `backend/demo/calls.py`, not from the timing agent: the two lanes are two different pipelines on the same clock.
