# frontend/premier-x/ — The Premier X

**Owner:** `CWT96`. The judged fan app, served at `/` by the FastAPI app (the earlier `frontend/fan-app/` is still at `/classic/`).

The screens are Steve's design templates used as they are. The source of truth is the Claude Design canvas (see the project doc `demo-ux-prototype.md`). Live data from `WS /demo/ws` and `GET /demo/squads` is bound into them (see `contracts/README.md`, "Demo mode").

## Run

```bash
uvicorn backend.app.main:app --port 8000     # then open http://localhost:8000/ (phone size: 390 × 844)
```

There is no build step and no npm. The fonts are in `assets/fonts/`, so the app needs no outside network.

## Files

| File | What it is |
|---|---|
| `index.html` | The page. It holds a 390 × 844 stage that scales to the window. |
| `pitch.js` | The live pitch, frame by frame: players run to their next spot (on time if they can, never faster than a sprint, picking up from where they really are), the ball flies on passes and shots and otherwise sits at the holder's feet, the ring follows. |
| `dc.js` | A small runtime for the `.dc.html` format: `{{bindings}}`, `<sc-for>`, `<sc-if>`, `onClick`/`onChange`, `ref`, `key`. Each render is morphed into the live DOM, so CSS transitions and entrance animations run as on the canvas. |
| `app.js` | Shared state, the `/demo/ws` connection, server messages into state, and navigation. A design link such as `href="Pick.dc.html"` moves to that screen. `data-go="start"` and `data-go="restart"` run an action instead. |
| `screens/*.dc.html` | The canvas artboards: Main, Pick, Live, Forecast, HalfTime, Result, Signup. The markup is the design's. Only the hard-coded demo values became `{{bindings}}`, and each `Component` reads `app.state`. |
| `assets/crests/` | Club crests. |
| `assets/fonts/` | Barlow Condensed and Inter (SIL OFL). |

## Changes from the canvas

- **Name:** "Matchday Call" is now **The Premier X**.
- **Data:** demo numbers are bound to live data: clock, score, points, room code, friends, the call card, forecasts, half-time and full-time numbers, the rank stamp and the badges.
- **Live pitch:** it shows the server's `demo.shape` dots and the ball from `demo.event`, not the canvas simulation.
- **Live cards:** one card style on the pitch (same as the points pop-up) for a cut in play (the clip title and "LIVE"), the half-time whistle and the full-time whistle; screens fade in, and the full-time count starts on its own.
- **Forecast banner:** it shows the latest forecast that **hit**, following the design's "positive feedback only" rule.
- **Kept static, as agreed:**
  - The sync chip.
  - The landing's room-code box.
- **Other matches:** Pick shows the design's three matches with their squads (from `GET /demo/squads`). Only Northbridge v Calder has match data: starting from another match plays it as the side in the same place (home → Northbridge, away → Calder).
- **Pitch:** each `demo.shape` is where everyone is at the next on-ball moment, with the time to get there; `pitch.js` moves them every frame (no CSS transitions), so passes land on the receiver, goals end in the net, and a cue mid-run never teleports anyone. A dribble is drawn as a take-on: a defender stands up to him, he steps to the side and bursts past. Coordinates map onto the drawn pitch lines.
- **Removed:** "End match (demo)" (the match now ends by itself).
