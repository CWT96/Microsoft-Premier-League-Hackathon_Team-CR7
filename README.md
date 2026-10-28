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

The Premier X is a second-screen game (an app you play on your phone while the match is on the TV) for the friend who came for the company, not the football. You scan a QR code, pick a side and play. No download, no account, no football knowledge needed.

The match drives the game. Whenever the ball is played to a player, an **AI call** pops up: what's his next move, **PASS**, **SHOOT** or **DRIBBLE** (run past an opponent with the ball)? You tap before he acts. The call locks the moment he plays it, and the points land as it happens. There is no countdown and no "next" button. On a second tab you make bigger, slower **forecasts** ("Will anyone score in the next 15 minutes?", the full-time result). You can also **back up to two players**, who earn you bonus points for what they really do. Your points, your streak and your friends' scores run live at the top of the screen.

**Points only ever go up.** A wrong call still earns points, a slow tap shows "Too quick!" and keeps your streak, and nothing is staked: no money, no betting.

**Why the Premier League would want it.** People who watch without following the game leave at full time with nothing to come back for, and the league never learns they were there. The Premier X asks for an email at two moments when the player has something to keep: at half time ("save your progress", +500 points) and after the full-time count ("save my points", +500 welcome bonus). The partner report then shows a club or league marketing team how many people played and signed up, and when.

## What you'll see (about 2 minutes)

The demo plays a ~93-second highlights cut of one match, shown as **Northbridge 2–1 Calder**. Best on a phone, or a browser window about 390 px wide.

1. **Landing** (what you see after scanning the QR code). The live match card, how many people are calling this match, **PLAY THIS MATCH**, and a box to join a friend's room by code. "Points only. No money, no betting."
2. **Pick your side.** Choose a match and a team, then optionally **back up to 2 players** from the matchday squad, grouped into starting XI and bench. Backing a player is not a prediction: each one earns from real match stats by position, and the card shows how.
   - Goalkeeper: penalty save, clean sheet, saves, claimed crosses.
   - Defender: goal, clean sheet, blocked shots, tackles won, interceptions, clearances.
   - Midfielder: goal, assist, shots on target, key passes, tackles won, interceptions.
   - Forward: goal, assist, big chances made, shots on target, dribbles past, fouls won.
3. **Live, Real time tab.** A 2D pitch with 22 players who run at football speed (never faster than a sprint, so nobody jumps) and a ball that flies on passes and shots. The score bar, your points, your rank and your friends Mia and Dan sit on top. When the ball reaches a player, the AI call card opens with one line on the situation. Tap PASS, SHOOT or DRIBBLE before he plays it. Then the answer, a one-sentence explanation and the points pop up, and the call history fades after a few seconds.
   - +10 for any call, +100 more if you're right, and +50 per step from the 3rd correct call in a row.
   - Too slow: "Too quick!", 0 points, streak kept. Nothing is ever taken away.
4. **Live, Forecast tab.** Bigger calls for bigger points, settled from what really happens in the match (including minutes the highlights skip):
   - next 5 minutes, "Who has more of the ball?" +500
   - next 10 minutes, "Who takes the next shot?" +1,000
   - next 15 minutes, "Will anyone score?" +1,500
   - full-time result +3,000, and the exact score +2,000 more. The result and the score are linked: change one and the other follows.
5. **Half time.** The whistle shows on the pitch, then the half-time screen: your first half split into calls, forecasts and player bonus, the room table, and a countdown to the second half. A sheet asks you to **save your progress** with just an email (+500). "Maybe later" closes it.
6. **Full time.** Your total counts up row by row (real-time calls, forecasts, player bonus), with your backed players' stats. Then come the badges (ORACLE for the right result, ON FIRE for 3+ calls in a row, GOOD EYE when a player you backed scores), a stamp with your place in the room and against a simulated crowd of 1,000 players (for example "TOP 19% TONIGHT"), and the room leaderboard with Mia, Dan and Leo.
7. **Save my points.** Email only, no password. +500 welcome bonus, your season total and your team's next fixture.
8. **Partner report** (`/ops/`). Open it after the phone flow: QR scans, players, calls and sign-ups, a minute-by-minute replay of the match with goals and half time marked, sign-ups per minute (a peak at the half-time save, a bigger one after full time), the funnel from scan to sign-up, and a table per QR channel.

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
- **AI call lines.** For each call, an LLM writes a short setup line ("Duarte has the ball in his own half for Northbridge. What will he do next?") and a one-sentence explanation after it locks. They were generated once, offline, with `gpt-4.1-mini` on **Azure OpenAI**, through `backend/agents/llm.py`, and committed to `backend/llm_cache/demo_lines/`. Guards reject any setup that hints at the answer and any line with a number that is not in the facts; a rejected line falls back to a template.
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

- **The data is synthetic.** The match is a generated dataset (`nbr-cal-seed42`) between two fictional clubs, Northbridge vs Calder; every club, crest and player name in the app is fictional. The events did not happen in a real match.
- **Off-ball player positions are estimated.** The dataset has no tracking data, so the 22 dots are placed from each team's formation and the ball position. Only the ball and the player on it come from the events.
- **The email save is a mock.** The address is checked for shape and dropped. Nothing is sent or stored.
- **The friends and the crowd are simulated** in code (fixed seeds), so every run is reproducible.
- **The partner report runs on simulated users.** `frontend/ops-dashboard/sim/simulate.js` is a seeded model of about 19,500 people (scan time and channel, calls, forecasts, leaving early, signing up). It is not wired to the backend yet.
- **Two controls are drawn but not working yet:** the screen-sync chip (TV, streaming or stadium) and the room-code box on the landing screen.
- **Only Northbridge v Calder has match data.** The Pick screen shows three matches from the design; starting another one plays the Northbridge v Calder data.

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
