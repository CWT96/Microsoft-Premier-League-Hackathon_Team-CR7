# Team CR7: The Premier X

Microsoft × Premier League *Inside the Game* Developer Hackathon. Submission deadline **2026-10-27 23:59 PT**.

**Live demo (open on a phone, or a narrow browser window):**
https://matchday-call.livelyforest-089ca771.eastus.azurecontainerapps.io

| Path | What |
|---|---|
| `/` | **The Premier X**, the judged phone app |
| `/ops/` | Partner report: what a club or league marketing team sees after the match |
| `/classic/` | The earlier fan app, kept for reference |

The web address still says `matchday-call`, the product's name before 2026-10-27. Renaming the Azure resources would break the deploy, so they keep the old name.

## What it is

The Premier X is a second-screen game (an app you play on your phone while the match is on the TV) for the friend who came for the company, not the football. Every few seconds the phone asks one simple thing about what is happening right now: a player has just received the ball, so will he **pass** it, **shoot**, or **dribble** (run past an opponent with it)? You tap a guess before he acts, and you see straight away whether you were right. Alongside that, you can make bigger, slower predictions ("Will anyone score in the next 15 minutes?") and back up to two players who earn you points for what they do. At full time you get a score, badges and your rank among friends. No football knowledge needed: the questions explain themselves, and every result comes with one plain sentence on what happened.

**Why the Premier League would want it.** People who watch without following the game leave at full time with nothing to come back for, and the league never learns they were there. The Premier X asks for an email twice: at half time ("save your progress", +500 points) and at full time, once the player has points worth keeping. Points never go down and nothing is staked, so it plays like a game, not a bet.

## What you'll see (about 90 seconds)

The demo plays a ~93-second highlights cut of one match, shown as **Arsenal 2–1 Chelsea**.

1. **Pick a match and a side** (Arsenal or Chelsea), then optionally **back up to 2 players**. Each position earns from different things (a goalkeeper from saves and clean sheets, a forward from goals and shots); the table is on screen.
2. **Kick off.** A small pitch shows 22 player dots and the ball. A score bar and clock run at the top.
3. **Live calls.** When the ball is passed to a player, a card pops up: *PASS / SHOOT / DRIBBLE*. It locks the moment he acts. Then you see the answer, a one-line explanation and your points.
   - +10 for answering, +100 more if right, and a streak bonus (+50 per step from the 3rd correct call in a row).
   - No answer in time shows "Too quick!" and scores 0. Nothing is ever taken away.
4. **Forecast tab.** Bigger predictions that settle from what really happens in the match, including minutes the highlights skip:
   - next 5 minutes, "Who has more of the ball?" +500
   - next 10 minutes, "Who takes the next shot?" +1,000
   - next 15 minutes, "Will anyone score?" +1,500
   - full-time result +3,000, plus +2,000 for the exact score (locks when the second half starts)
5. **Half time.** Your points so far, your backed players' first-half bonus, the room table, and a "save your progress" sheet (email, +500 once).
6. **Full time.** Your total counts up row by row (calls, forecasts, player bonus), then badges (ORACLE for the right result, ON FIRE for 3+ calls in a row, GOOD EYE for calling a goal), a stamp such as "#1 IN ROOM · TOP 19%" against a simulated crowd of 1,000 players, and the room leaderboard with three simulated friends (Mia, Dan and Leo). Then the email-only sign-up (+500 welcome bonus).
7. **Partner report** (`/ops/`). Open it after the phone flow: QR scans, players, calls and sign-ups, a minute-by-minute replay of the match with goals and half time marked, sign-ups per minute (a peak at the half-time save, a bigger one after full time), the funnel from scan to sign-up, and a table per QR channel.

## How it works

```
match events ─► decision moments (code) ─► live calls ─────┐
      │                                    forecasts ──────┼─► points (code) ─► phone
      │                                    player bonus ───┘
      └─► facts per call (code) ─► call lines (LLM, offline, cached, guarded) ─► phone
```

- **Data.** One match's raw event log (every pass, shot, save, with pitch coordinates) in StatsBomb format.
- **Decision moments.** Code scans the events for each time the ball reaches a player, and takes the answer from what that player does next (pass, shot or dribble). Calls open on the pass to him and lock on his action, so the answer cannot be known when you tap.
- **Forecasts and player bonus.** Code settles each forecast window from the events in that span, and counts each backed player's stats (saves, tackles, key passes, goals…) against the position's scoring table.
- **Points.** Pure functions in code. The total is shown live during the match.
- **AI call lines.** For each call, an LLM writes a short setup line ("Gabriel has the ball in his own half for Arsenal. What will he do next?") and a one-sentence explanation after it locks. They were generated once, offline, with `gpt-4.1-mini` on **Azure OpenAI**, through `backend/agents/llm.py`, and committed to `backend/llm_cache/demo_lines/`. Guards reject any setup that hints at the answer and any line with a number that is not in the facts; a rejected line falls back to a template.
- **Core rule: code decides, the LLM phrases.** The LLM never picks an answer or a number.

## Architecture

- **One FastAPI process** (Python 3.11). In-memory state; each phone gets its own private run, so many judges can play at once (up to 50 sockets by default).
- **`WS /demo/ws`** streams the run to the phone: clock, events, player dots, calls, forecasts, results. **`GET /demo/squads`** lists both squads and the scoring table. Full protocol: [`contracts/README.md`](contracts/README.md#demo-mode-demows-and-demosquads).
- **Static frontends**, plain HTML/CSS/JS with no build step, served by the same process:
  - `frontend/premier-x/` at `/`: the judged app. Its screens are the design canvas artboards, with live data bound in.
  - `frontend/ops-dashboard/` at `/ops/`: the partner report.
  - `frontend/fan-app/` at `/classic/`: the earlier fan app.
- **Azure Container Apps**, one container, `LLM_MODE=cache`: the deployed app only reads the committed cache, holds no API key and makes no LLM calls.

## Honest notes

- **The data is synthetic.** The match is a generated dataset (`nbr-cal-seed42`) between two fictional clubs, relabelled for the demo as Arsenal vs Chelsea with real player names. The events did not happen in a real match.
- **Off-ball player positions are estimated.** The dataset has no tracking data, so the 22 dots are placed from each team's formation and the ball position. Only the ball and the player on it come from the events.
- **The email save is a mock.** The address is checked for shape and dropped. Nothing is sent or stored.
- **The friends and the crowd are simulated** in code (fixed seeds), so every run is reproducible.
- **The partner report runs on simulated users.** `frontend/ops-dashboard/sim/simulate.js` is a seeded model of about 19,500 people (scan time and channel, calls, forecasts, leaving early, signing up). It is not wired to the backend yet.
- **Two controls are drawn but not working yet:** the screen-sync chip (TV, streaming or stadium) and the room-code box on the landing screen.
- **Only Arsenal v Chelsea has match data.** The Pick screen shows three matches from the design; starting another one plays the Arsenal v Chelsea data.

## Run locally

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements.txt
uvicorn backend.app.main:app --reload     # open http://localhost:8000 (single worker only)
pytest                                    # fast suite
pytest -m slow                            # full-match WebSocket end-to-end run (~3 min)
```

No keys are needed: `LLM_MODE` defaults to `cache`, which replays the committed lines.

## Deploy

See [`infra/README.md`](infra/README.md): build the image from `infra/Dockerfile`, push it to Azure Container Registry, update the Container App. It also has the command to regenerate the AI call lines with Azure OpenAI.

## Repo map

| Path | What |
|---|---|
| `frontend/premier-x/` | The judged phone app, The Premier X (static HTML/CSS/JS), served at `/` |
| `frontend/ops-dashboard/` | Partner report on simulated users, served at `/ops/` |
| `frontend/fan-app/` | The earlier fan app, served at `/classic/` |
| `backend/demo/` | Demo mode: calls, timeline, forecasts, player bonus, room and result, `/demo/ws` + `/demo/squads` |
| `backend/llm_cache/demo_lines/` | Committed AI call lines (Azure OpenAI, `gpt-4.1-mini`) |
| `backend/agents/llm.py` | The one LLM client: cache / live modes, safety and number guards |
| `backend/app/` | FastAPI app: mounts the three frontends and `/demo/ws`; also the earlier `/ws` Room flow and `/api/*` |
| `backend/ingest/`, `detect/`, `bank/`, `model/`, `resolve/`, `scoring/`, `social/` | Earlier prototype pipeline (see below) |
| `backend/tests/` | pytest suite |
| `contracts/` | WebSocket and REST payloads, including demo mode |
| `data/source/` | The synthetic match dataset (StatsBomb format); `data/generator/` builds it |
| `docs/superpowers/specs/2026-10-27-fan-app-demo-design.md` | Fan app design spec; plans in `docs/superpowers/plans/2026-10-27-fan-app-*.md` |
| `docs/pipeline.md` | Design of the earlier prototype |
| `infra/` | Dockerfile and Azure Container Apps notes |
| `CLAUDE.md` | Folder rules, invariants and git workflow for contributors |

## What's next

- **Screen sync:** hold each call back by the viewer's delay (TV about 6 s, streaming about 30 s, stadium none), so the phone never spoils a moment.
- **A licensed live data feed** in place of the replayed dataset.
- **Real rooms**, so friends play the same match together, and a real email save.
- **The partner report on live data**, from the backend's funnel endpoint instead of the simulation.

## Earlier prototype

Before The Premier X (called Matchday Call until 2026-10-27), the team built a "Room" flow: two-option yes/no questions during dull spells, catch-up cards after attacks, and a 0–1000 score card shown only at full time. Its backend (`/ws`, `/api/*`, `backend/app/replay.py` and the pipeline folders above) is still in the repo, unchanged, and documented in `docs/pipeline.md` and the first sections of `contracts/README.md`. The judged demo does not use it.

## Team

Workflow / backend: `stephenwzkong`, `tianawangty`. UI/UX / frontend: `CWT96`.
