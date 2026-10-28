# Fan phone app — hackathon demo design

Date: 2026-10-27 · Owner: stephenwzkong · Frontend area owner: CWT96 (heads-up needed)

## Intent

Judges pick up a phone, play one match from kickoff, and in about 3 minutes see the whole fan experience: real-time calls, a goal, the half-time save, and the full-time payoff. It is a demo, not a product. It must look and play like the design in `frontend/demo-ux`, and it must be driven by the real backend running on the synthetic match data, so the AI part is genuine.

## Goals

- One judge, one phone, 3 minutes, start to finish with no taps needed to move the match on.
- Visually faithful to `frontend/demo-ux` (layout, copy, colours, crests, motion, points model).
- Every call, lock, result and point comes from the backend, computed from `data/source/` match 9000001.
- Deployable as one image to Azure Container Apps (see `infra/README.md`).

## Non-goals

- A real, production-grade app: no accounts, no persistence, no real email, no scaling.
- Several judges in one room, or other matches (the design's Liverpool/Man City and Newcastle/Tottenham cards may show but are not playable).
- The backend's yes/no question stream, player questions, hint button, catch-up cards and crowd split.

## Anti-goals (failure even if it works)

- A judge waits with nothing happening for more than ~15 s.
- "Always pick PASS" is the winning strategy.
- The phone and the partner report (`frontend/ops-dashboard`, the 07b design) tell different match stories.
- Fast-forwarded play. The ball always moves at normal speed; time passes only by visible jumps.

## Decisions (made by the user)

| # | Area | Decision |
|---|---|---|
| 1 | Calls | Only PASS / SHOOT / DRIBBLE calls at decision moments. Yes/no questions are dropped. |
| 2 | Timing | No countdown. A call locks when the ball moves. A user who had not answered sees "Too quick!", loses nothing, keeps the streak. |
| 3 | Points | The design's fixed table (below), shown live as pop-ups, with the design's full-time count-up. |
| 4 | Forecasts | A separate Forecast tab the user opens any time: next 5 / 10 / 15 minutes, full-time result, linked exact score. The backend settles them. |
| 5 | Players | Back up to 2 players from the matchday squad; they earn a passive bonus from how they actually play. No player questions. |
| 6 | Teams | Match 9000001 is relabelled: Northbridge FC → Arsenal, Calder Town → Chelsea, with the design's crests and squads (`frontend/demo-ux/screens/Pick.dc.html`, `squads()` "m1-home"/"m1-away"). The 23' goal is scored by Saka. |
| 7 | Extras | Hint, catch-up cards and crowd split stay out. Follow the design. |
| 8 | Flow | Judge plays from kickoff. The match is a 3-minute highlights cut (below). Jumps are automatic. |
| 9 | Room | One judge per room, with the backend's simulated bot friends. |
| 10 | Backend | The phone is driven by the real backend; its input is the synthetic data. |

### Points table (from `frontend/demo-ux/README.md`)

Never minus. Shown live.

| Moment | Points |
|---|---|
| Any real-time call | +10 |
| Correct real-time call | +100 |
| Streak of 3+ correct calls | +50 per step |
| Forecast next 5 / 10 / 15 min | +500 / +1,000 / +1,500 |
| Full-time result (+ exact score) | +3,000 (+2,000) |
| Welcome bonus on sign-up | +500 |
| Backed player bonus | per position, as drawn on the Pick screen |

### The 3-minute highlights cut

Each segment plays at 1×; the match clock visibly jumps between segments.

| Real time | Plays | The judge |
|---|---|---|
| 0:00–0:40 | Kickoff and the opening minutes | First calls; can open a forecast |
| 0:40–1:20 | Build-up to Saka's goal (23') | Calls during the attack; goal; points pop |
| 1:20–1:50 | Half time | The "save your points" sheet slides up |
| 1:50–2:30 | Chelsea's equaliser (60') and Arsenal's winner (79') | More calls; forecasts settle |
| 2:30–3:00 | Full time | Count-up, rank stamp, badges, room leaderboard |

Segment boundaries are a fixed script for match 9000001. Times are targets, not exact.

## Calls up to the builder (made by Claude; the user can override)

- **Choosing decision moments.** Built from the synthetic events (each match has ~220–310 final-third passes/carries and ~60 shots/dribbles). Prefer moments where the outcome is genuinely uncertain, so a fixed +100 does not reward always picking PASS. Aim for a call every ~15–20 s of real time inside each segment.
- **Settling a call.** From the on-ball player's next action: pass, shot, or dribble/carry. The lock is that action.
- **Scorers.** 23' → Saka (Saka takes the synthetic centre-forward's slot; Gyökeres takes the synthetic right winger's). 60' → João Pedro. 79' (synthetic right centre-back) → Saliba. Everyone else maps by position onto the design's squads; spare synthetic substitutes take leftover names or stay off the sheet.
- **Partner report alignment.** Shift the partner report's goal minutes from 18'/54'/77' to 23'/60'/79', rather than regenerating the match data (which is calibrated to the question bank).
- **Frontend stack.** Plain HTML, CSS and JavaScript, no build step (like `frontend/ops-dashboard`), in `frontend/fan-app/`, served by the FastAPI app.
- **Sign-up.** Half-time and full-time email saves use the existing mock `POST /api/signup`; nothing is emailed or stored beyond memory.
- **Pitch.** The dataset has no tracking (`backend/ingest/loader.py`), so the design's moving 2D pitch is animated from event coordinates (ball path, acting player). Other players are approximate.
- **Contracts.** The 2-option limit in `contracts/question.schema.json` and the "never show points" rule in `contracts/README.md` change to match this design. Record the changes in a `contracts/` change so CWT96 and Tiana see them.

- **Demo room mode, beside the existing Room.** The demo runs as a separate room mode; the existing yes/no flow, its tests and the ops funnel endpoint stay as they are. Safer on deadline day than rewriting `Room`.
- **Forecasts on a jumping match.** Forecast windows are sized to settle inside a segment the judge watches (not by skipped minutes). The design's three window questions are offered again after each settles. The full-time result and exact score lock at half time and settle at full time.
- **Goals.** Already at 23' / 60' / 79' with a 2–1 final score in `data/source/events/9000001.json`; no data change needed.
- **Replay speed.** The demo plays at 1× inside segments, overriding the default `REPLAY_SPEED=15`.

## Where the backend is today (for the builder)

- `backend/app/replay.py` — one Room per room: ticks, pacers, resolver, scoring, bots, pauses, `skip`.
- `backend/agents/question_generator.py` + `backend/bank/` — today's yes/no question selection.
- `backend/resolve/watcher.py` — settles questions from events.
- `backend/scoring/scoring.py` — today's formula; the demo uses the fixed table instead.
- `backend/social/bots.py` — three bot friends in every room.
- `backend/app/main.py` — REST + `/ws`; must run as one worker.

## Constraints

- Free tier only: Azure Container Apps consumption plan, 1 replica, `LLM_MODE=cache`.
- State in memory; restarting the app restarts the match.
- Python 3.11. All existing backend tests keep passing, or are updated deliberately where the demo changes behaviour.

## Conflicts with written team rules (decided by the user, 2026-10-27)

These decisions break rules in the repo's `CLAUDE.md`, `README.md` and `docs/pipeline.md`. The user chose to keep real club and player names, the 3-option calls and live points, and to merge without waiting for approvals. All names live in one file so they can be swapped back to fictional ones quickly.

| Decision | Team rule it breaks |
|---|---|
| 6: Arsenal vs Chelsea, real player names | "Synthetic data and fictional clubs only" (`CLAUDE.md` invariants); "the clubs and players are fictional" (`README.md`); "Branding: clubs and players are fictional" (`docs/pipeline.md`); PR checklist "no real people's data" |
| 1: 3-option calls | "Every question has exactly 2 options" (`CLAUDE.md` invariants) |
| 3: live points | "No point numbers or running total on screen during the match" (`CLAUDE.md` invariants, `contracts/README.md`) |
| Contract changes | Contracts frozen since 10/25; changes additive only, approved by a backend owner and CWT96 |

## Deadline and build order (decided by the user)

Submission is due 2026-10-27 23:59 PT (`README.md`). Build in this order and stop wherever time runs out; each step must leave a working, deployed demo:

1. Deploy the current backend to Azure (Dockerfile, Container Apps). The user has a new Azure account.
2. Phone app: live PASS / SHOOT / DRIBBLE calls and live points (demo room mode, highlights cut, relabelled match).
3. Half time (save sheet) and full time (count-up, leaderboard).
4. Forecast tab.
5. Player bonus.

**Wording is template-based, no LLM.** Code and the existing probability model choose and settle moments; fixed templates phrase them. The pitch to judges says the AI spots the decision moments.

**Approvals.** The user merges on their own approval; no wait for CWT96 or Tiana. Heads-up only.

## Open items

- Heads-up to CWT96 (frontend owner) and Tiana (backend co-owner) before code lands.
