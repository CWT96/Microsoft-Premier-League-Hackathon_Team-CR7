# Team CR7: Quick-Play

**Bored when it's slow, lost when it's loud. We fix both for the friend who doesn't watch football.**

Microsoft × Premier League *Inside the Game* Developer Hackathon. Submission deadline **2026-10-27 23:59 PT**.

## The problem

Most people in a room watching football are not football fans. They came for the company. For them, a match has two bad modes:
- **Slow:** long spells of passing in midfield, when nothing seems to happen and the phone comes out for something else.
- **Loud:** a sudden attack, everyone shouts, and they have no idea why.

## What Quick-Play does

The non-fan scans a QR code on the table, picks a team and a favourite player, and their phone becomes a second screen:

| Match moment | What the phone does |
|---|---|
| Dull stretch | Pops a quick **two-option question** ("Next pass: forward or back?"). 6 seconds to answer, optional AI hint. |
| Attack | Goes quiet so everyone watches the TV. When the attack ends, shows a **catch-up card**: a 2D replay plus one plain sentence. |
| Kickoff / half time | **Forecast** questions for the whole match, plus questions about *your* player |
| Full time | **Score card** (0–1000) → email sign-up → **membership card** with a QR code for partner-store rewards |

Everyone in the room answers the same question at the same moment, so they see how their friends voted. That is where the conversation starts.

## How it works

```
Match dataset ─► Detector (code) ─► Timing agent ─► Question generator ─► phones
                                           │                 ▲ probabilities, fair lines (code)
   events ─► Resolver (code) ─► Explainer agent ─► result / catch-up card ─► phones
                     └─► Scoring (code) ─► Scoring agent (summary) ─► score card
```

**Core rule:** code decides what happened and how many points it is worth. LLM agents on Microsoft Foundry decide *when* to ask, *what* to ask and *how* to say it. They cannot change a result or a number. Every number they write is checked against the facts.

### Three kinds of questions, all with two options
- **Instant:** settles in seconds, asked during dull stretches.
- **Forecast:** settles at half time or full time.
- **Favourite player:** about the player you picked, in instant and forecast versions. A goalkeeper and a striker play very different matches, so the probability model tunes each player's question to roughly 50/50. The goalkeeper gets "touch the ball in the next 3 minutes?" while the striker gets "…in the next 90 seconds?". Every player gets the same count at the same difficulty.

### Scoring

The same formula applies to everyone, with no handicaps:

```
per question  = category budget / questions in that category   (instant 400, forecast 300, player 300)
correct answer earns  per question × hint (1.0, or 0.5 if you peeked) × streak (1.0 → 1.2 → 1.5)
final score   = 1000 × earned / perfect-run total
```

During the match you only see ✓ and coins. The number appears at full time.

### Social
Three scopes:
- **Room:** everyone who scanned the same QR, live over WebSocket.
- **Friends:** bot friends, so a solo player still has company.
- **Global:** how everyone else watching this match voted. In this prototype the global crowd is **simulated**, because each room replays the match at its own speed.

### Enterprise value
The paying customer is a league or club marketing team. An **ops dashboard** shows how many non-fans scanned, played, signed up and redeemed rewards. In short, the product turns casual viewers into registered members.

## Status and scope (3 people, 3 days)

| Tier | Scope | Cut-off |
|---|---|---|
| **P0** | Synthetic match → dull detector → instant questions → resolver → scoring → 2D pitch, question card, score card; bot friends | 10/25 |
| **P1** | AI hint, forecast and favourite-player questions, catch-up card, Azure deploy | 10/26 |
| **P2** | Real multi-user rooms, global crowd, sign-up + membership card, ops dashboard (static if late) | 10/27 noon |

All data is synthetic, and the clubs and players are fictional:
- **Match data** comes from a simulated match dataset in `data/source/`, which a teammate is uploading.
- **User behaviour** comes from code: the global crowd, bot friends and the sign-up funnel.

Email, wallet pass and rewards are mocked. LLM outputs are cached, so the demo runs at zero cost and replays the same way every time.

## Judging map

| Criterion (20% each) | Where to look |
|---|---|
| Technical implementation | Fixed simulated match dataset with labelled dull stretches (generator ground truth) (detector hits and misses reported); simulated users; schema contracts in `contracts/`; tests in `backend/tests/` |
| Agent design | 4 Foundry agents with tools (timing, question generator, explainer, scoring summary). See `docs/pipeline.md` § Agents |
| Real-world impact | Non-fans as the target user; sign-up funnel for clubs |
| UX / demo | Mobile second screen, phone goes quiet during attacks, catch-up card, under-2-minute video |
| Category fit | Enterprise application (ops funnel) + Multi-agent system |

## Repo map

| Path | What |
|---|---|
| `docs/pipeline.md` | Full design: every stage, the template catalog, the scoring formula, a worked example, the demo script |
| `contracts/` | WebSocket/REST payloads shared by backend and frontend |
| `backend/` | Python + FastAPI: data ingest, detector, model, resolver, scoring, agents, simulated users |
| `frontend/` | Phone UI |
| `data/source/` | Simulated match dataset (**upload here**; rules in `data/README.md`) |
| `infra/` | Env vars, Docker, Azure Container Apps |
| `docs/source_reports/` | Hackathon rules summary and landscape of other projects |
| `CLAUDE.md` | Folder rules, interfaces, infrastructure and git workflow for contributors |

## Run

The code is not written yet. Commands will appear here and in `backend/README.md` and `frontend/README.md` as they land.

## Team

Workflow / backend: `stephenwzkong`, `tianawangty`. UI/UX / frontend: `CWT96`.
