# frontend/

**Owner:** `CWT96`. Framework is CWT96's choice. Record it here once picked, with the run command.

The frontend talks to the backend only through `../contracts/` (WebSocket `/ws` + `/api/*`). Ask in a `contracts/` PR if a field is missing; do not work around it.

## Screens (priority from the root README)

| Screen | Tier | Data source |
|---|---|---|
| Entry: pick match → team → favourite player | P0 | `GET /api/matches`, `GET /api/matches/{id}/players`, WS `join` |
| Match view: 2D pitch + score/clock + mode indicator | P0 | WS `match.event`, `match.state` |
| Question card (2 buttons, 6s lock countdown) | P0 | WS `question.open`, `question.locked` |
| Hint button on the question card (tap to reveal, "costs half points") | P1 | WS `hint.request` → `hint` |
| Kickoff / half-time stack: 3 cards to swipe, 60s lock | P1 | WS `question.open` (several at once) |
| Result toast: ✓/coins animation, message, explanation, crowd split | P0 | WS `question.result` |
| Final score card + leaderboards (room / friends / global) | P0 | WS `score.final`, `leaderboard` |
| Demo controls: speed, skip to next dull / HT / FT | P0 | WS `control` |
| Catch-up card: 2D replay of 3–5 actions + one line | P1 | WS `catchup.card` + cached `match.event`s |
| Room QR (create/share) | P2 | `POST /api/rooms` |
| Email sign-up → web wallet card with QR + "Add to Wallet" button (mock) | P2 | `POST /api/signup`, `GET /api/card/{id}` |
| Ops funnel dashboard | P2, built as a static page in [`ops-dashboard/`](ops-dashboard/README.md) on simulated data | `GET /api/ops/funnel` (not wired yet) |

## UI rules from the design
- Never show point numbers or a running total during the match. Only ✓ and coin animations.
- Never show the word "void". Show `message` as is.
- Questions disappear the moment `match.state.mode` becomes `attack`.
- Mobile-first: one thumb, two big buttons, readable in 2 seconds.
