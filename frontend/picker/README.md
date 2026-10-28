# frontend/picker/

**Owner:** `stephenwzkong`. The call picker: a page for judges that shows how the app picks each call in the fan's highlights run.

Plain HTML, CSS and JavaScript. No build step, no framework, no external network.

## Run

Served by the backend at `/picker/` (it reads `GET /demo/director?team=NBR|CAL`):

```bash
uvicorn backend.app.main:app --reload    # http://localhost:8000/picker/
```

URL options:

| Option | Effect |
|---|---|
| `?team=CAL` | Start on the Calder fan's cut (default Northbridge) |
| `?t=47.5` | Start with the playhead at that real second of the cut |
| `?data=picker-{team}.json` | Read a stand-in JSON instead of the endpoint (same origin only; `{team}` is replaced) |

## What is on the page

- **Pitch** (top left): the 22 players from the latest `demo.shape` at or before the playhead, gliding between shapes; the ball from `demo.event`; the ball holder's name; the fan team's final third shaded (orange when the ball is in it). Jump cards, half time and full time cover the pitch.
- **Phone** (right): scoreboard, clock, the call card during a picked call's window (the right answer lights up after it locks, with the explain line for 5 s), a goal banner, else "Match on…".
- **Detail panel** (under the phone): the moment nearest the playhead, or the hovered / clicked marker: status, reason, the facts sent to the LLM and the lines it wrote.
- **Timeline** (under the pitch): ball movement, attack spans, picked windows, result holds and one marker per decision moment. Hover a marker for the reason; click it, or anywhere on the chart, to move the playhead.

Everything is derived from the cues with `at <= t`, so scrubbing is stateless. Every JSON field is guarded: a missing value hides its piece, it never throws.
