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
| `dc.js` | A small runtime for the `.dc.html` format: `{{bindings}}`, `<sc-for>`, `<sc-if>`, `onClick`/`onChange`, `ref`, `key`. Each render is morphed into the live DOM, so CSS transitions and entrance animations run as on the canvas. |
| `app.js` | Shared state, the `/demo/ws` connection, server messages into state, and navigation. A design link such as `href="Pick.dc.html"` moves to that screen. `data-go="start"` and `data-go="restart"` run an action instead. |
| `screens/*.dc.html` | The canvas artboards: Main, Pick, Live, Forecast, HalfTime, Result, Signup. The markup is the design's. Only the hard-coded demo values became `{{bindings}}`, and each `Component` reads `app.state`. |
| `assets/crests/` | Club crests. |
| `assets/fonts/` | Barlow Condensed and Inter (SIL OFL). |

## Changes from the canvas

- **Name:** "Matchday Call" is now **The Premier X**.
- **Data:** demo numbers are bound to live data: clock, score, points, room code, friends, the call card, forecasts, half-time and full-time numbers, the rank stamp and the badges.
- **Live pitch:** it shows the server's `demo.shape` dots and the ball from `demo.event`, not the canvas simulation.
- **Jump card:** a "jump" title card on the pitch (same style as the points pop-up) covers the cut between highlight clips.
- **Forecast banner:** it shows the latest forecast that **hit**, following the design's "positive feedback only" rule.
- **Kept static, as agreed:**
  - The sync chip.
  - The landing's room-code box.
- **Removed:**
  - "End match (demo)" (the match now ends by itself).
  - The two mock matches on Pick (the backend has one match).
