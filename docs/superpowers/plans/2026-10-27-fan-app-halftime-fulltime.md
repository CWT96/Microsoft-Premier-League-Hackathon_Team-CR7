# Fan App — Half-Time Save Sheet and Full-Time Result Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Team-doc items 8–9: a half-time "save your progress" sheet (email only, +500 once, room friends Mia/Dan/Leo with two already saved), and a full-time result screen (three score rows that count up, badges ORACLE / ON FIRE / GOOD EYE, a "#N IN ROOM · TOP X% TONIGHT" stamp, the room leaderboard, save CTA, play again).

**Architecture:** A new pure module `backend/demo/room.py` simulates the three friends and a crowd from the same timeline and scoring rules, and computes badges and the stamp. `DemoSession` adds the room/breakdown data to `demo.half_time` and `demo.full_time` (additive fields), accepts a `signup` message (+500 once, added to the shared total), and the router keeps the socket open up to 120 s after full time so a judge can save. The frontend replaces the minimal half-time/full-time cards with the design screens.

**Tech Stack:** Python 3.11, pytest; vanilla JS/CSS.

**Spec:** Team doc "粉丝 App 演示：后端改动说明" items 8–9 (bots renamed Mia, Dan, Leo, two "already saved"; signup +500 once; full time: three score rows, three badges ORACLE / ON FIRE / GOOD EYE, "room #1 · top 12%" stamp, room board). Design: `git show origin/frontend/demo-ux:frontend/demo-ux/screens/HalfTime.dc.html`, `Result.dc.html`, `Signup.dc.html` (copy, colours, count-up timing: each row 2.8 s ease-out, then stamp/badges/room ≈ 3.5 s total). Earlier spec: `docs/superpowers/specs/2026-10-27-fan-app-demo-design.md` ("Sign-up … mock … nothing is emailed or stored beyond memory").

## Global Constraints

- Friends (exact): `Mia` (call accuracy 0.70), `Dan` (0.50), `Leo` (0.35); each answers every call (never "too quick"), picks deterministic from `random.Random(f"{name}-{call_id}")`: correct with the given probability, otherwise a uniformly chosen wrong option; scored with `points.score_call` (streaks apply). Forecasts: each friend makes one f15 pick per Play at its first tick, correct with the same probability (scored with `forecasts.score_window` from the real outcome), and a final pick of NBR 2–1 (Mia), DRAW 1–1 (Dan), CAL 1–2 (Leo) settled like the user's. `saved`: Mia true, Dan true, Leo false.
- Crowd for the "TOP X%" stamp: 1000 simulated players, seed `"crowd"`, accuracy uniform in [0.2, 0.8] each, scored like friends but with a final pick uniform over NBR/DRAW/CAL and score 2–1/1–1/1–2 accordingly. `top_pct = max(1, ceil(100 * (#crowd with total >= you) / 1000))`.
- Room code: 4 chars from `ABCDEFGHJKLMNPQRSTUVWXYZ23456789`, random per session.
- Half-time data (additive on `demo.half_time`): `room: {code, players: [{name, pts, saved, me}] sorted by pts desc}`, `breakdown: {calls: int, forecasts: int}`, `you: {rank}`, `saved: bool`.
- Full-time data (additive on `demo.full_time`): `rows: [{key: "rt", label: "REAL-TIME CALLS", detail, points}, {key: "fc", label: "FORECAST", detail, points}, {key: "pb", label: "PLAYER BONUS", detail: "Back players next match", points: 0}]`; details: rt `"{n} correct calls · best streak of {k}"`, fc `"{h} forecasts hit"` + `" · full-time result nailed"` if the final result was right; `badges: [{title, text}]` earned only — ORACLE `"Called the full-time result"` (final result right), ON FIRE `"{k} calls in a row"` (best streak ≥ 3), GOOD EYE `"You called {scorer}'s goal"` (a correct call whose move ended in a goal; first such); `stamp: "#{rank} IN ROOM {code} · TOP {top_pct}% TONIGHT"`; `room` as at half time (final totals); `total`; `saved`.
- Signup: client `{"type": "signup", "data": {"email": str}}`; valid iff it matches `^[^\s@]+@[^\s@]+\.[^\s@]+$` and ≤ 254 chars. First valid one: total += 500, reply `signup.saved {bonus: 500, total}`; invalid → `signup.rejected {reason: "bad_email"}`; any later one → `signup.rejected {reason: "already"}`. Accepted any time after `start`, including after full time. The email is never logged, stored or sent anywhere (drop it after validation).
- After `demo.full_time` the router keeps the socket open until the client closes or 120 s pass (`POST_MATCH_S = 120`), then closes 1000. Session cap/cleanup behaviour otherwise unchanged.
- Frontend copy follows the design screens; honest demo copy: after saving, "We'd send a sign-in link to {email}. (Demo: nothing is sent.)". Half time is a bottom sheet over the paused pitch with: score, "Great first half. Save your progress.", room line (`"You lead Room {code}. Mia and Dan already saved theirs."` if rank 1 else `"{leader} leads Room {code}. Mia and Dan already saved theirs."`), three perks (Keep points if you leave / Season leaderboard / +500 welcome bonus), email field, "SAVE MY PROGRESS", "Maybe later", plus the existing "SET YOUR FULL-TIME PICK →" button and "Forecasts: +X" line. After "Maybe later" or the second half starting, a small "Save your points" chip stays in the HUD until saved. Full time: score, "YOUR MATCH SCORE" with "?" until counted, "COUNT MY POINTS" (rows count up 2.8 s each, ease-out; "Skip" jumps to the end), then stamp, badges, room board (≈ 3.5 s), "SAVE MY {total} POINTS" (opens the save form unless saved) and "Play again".
- Do not modify `backend/app/main.py`, `backend/ingest/`, `llm.py`, `replay.py`, `bank/`, `resolve/`, `scoring/`, `backend/social/` (the demo has its own friends), non-demo tests.
- Commits `[area] Verb thing: description` + blank line + `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Branch `backend/demo-live-calls`. Tests `.venv/bin/python -m pytest -q`.

## Review Focus

1. **Double +500** — a second signup (or one at half time and one at full time) never adds again. Test (Task 1).
2. **Email privacy** — the email never appears in logs, messages back, or stored state (except echoed to the client's own screen by the client). Test that no server message contains the email (Task 1).
3. **Post-match socket** — closes after 120 s idle and on client close; session slot released; Play again opens a new session (Task 1 test with a short injected timeout).
4. **Deterministic friends** — same friends' totals every run; the judge with perfect play is #1; a judge who never taps is last (Task 1).
5. **Full-time screen never stuck** — Skip works mid-count; Play again from any stage resets everything (Task 2).

---

### Task 1: Room, badges, signup (backend)

**Files:** Create `backend/demo/room.py`, `backend/tests/test_demo_room.py`; modify `backend/demo/session.py`, `backend/demo/router.py`, `backend/tests/test_demo_session.py` only for additive assertions.

- [ ] Tests first: friends deterministic and ordered sensibly (Mia's call accuracy shows in her total > Leo's over the 6 calls is NOT guaranteed — assert determinism and exact totals the implementation produces, recorded in the report); perfect user is rank 1, silent user rank 4; badges for a perfect run = ORACLE (with final NBR 2–1), ON FIRE ("6 calls in a row"? — use the real best streak), GOOD EYE ("You called Mensah's goal"); stamp format; half-time and full-time payload keys; signup once (+500), bad email, already; no message contains the email; post-match close after an injected short timeout. RED → implement → full suite + demo files 3× → commit `[backend] Add demo room: friends, badges, stamp and email save over /demo/ws`.

### Task 2: Half-time sheet and full-time screen (frontend)

**Files:** `frontend/fan-app/index.html`, `app.js`, `styles.css`.

- [ ] Implement per the constraints and the three design screens; textContent only; reset everything on Play again; `node --check`; static tests green; headless run if possible → commit `[frontend] Add fan app half-time save sheet and full-time result screen`.

### Task 3: Deploy

- [ ] Build/push `matchday-call:v7`, update, scripted live run (half-time room data, signup +500 once, full-time rows/badges/stamp, socket stays open then closes), Chrome check, push, PR comment.
