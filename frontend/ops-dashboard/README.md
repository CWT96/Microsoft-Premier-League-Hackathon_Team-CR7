# frontend/ops-dashboard/

**Owner:** `CWT96`. Tier **P2** (ops funnel dashboard, the Enterprise application story).

The partner-side view a club or league marketing team sees after a match: how many people scanned the QR code, how many played, when interaction spiked, and how many left an email at full time. In the demo video it runs right after the phone flow.

Plain HTML, CSS and JavaScript. No build step, no framework, no backend call yet.

## Run

Open `index.html` in a browser, or serve the folder:

```bash
cd frontend/ops-dashboard && python3 -m http.server 8080   # http://localhost:8080
```

URL options:

| Option | Effect |
|---|---|
| `?autoplay=1` | Start the match replay on load |
| `&seconds=16` | Replay length in seconds (it also pauses briefly at goals, half time and full time) |
| `&demo=0` | Hide the "DEMO DATA" tag |

## What is on the page

- **Headline:** total sign-ups from the match, share of players who signed up, share who were new to a Premier League account.
- **Snapshot:** QR scans, players, playing right now, calls made, sign-ups. These follow the replay playhead.
- **Match timeline:** players online, calls per minute and total sign-ups, with goals, half time and full time marked. **Replay match** animates it; the slider scrubs to any minute.
- **Funnel:** scanned → started → made a call → made a forecast → stayed to full time → saved points with an email.
- **Channels:** sign-up rate by where the QR code was scanned (friend room invites, partner pubs, TV broadcast, stadium screen).
- **Takeaways:** call lift after a goal, share of sign-ups in the 10 minutes after full time, room-invite conversion vs walk-up scans.

## Data (simulated)

All numbers are **simulated user behaviour**, not real data. `sim/simulate.js` is a seeded per-user model: each simulated person has a scan time and channel, may start a game, makes calls and forecasts, may leave early, and may sign up. Sign-ups come almost entirely from the full-time "save your points" screen; a small number come from an assumed "save before you leave" prompt.

```bash
node frontend/ops-dashboard/sim/simulate.js
```

This rewrites, deterministically (same seed, same output):

| File | What |
|---|---|
| `data/data.js` | Aggregates the page loads (`window.SIM`) |
| `data/matchday_call_minute_by_minute.csv` | One row per match minute: scans, games started, players online, calls, sign-ups |
| `data/matchday_call_users.csv` | One row per simulated person (19,559 rows) |

The project rule is that user data is generated in `backend/social/` and served at `GET /api/ops/funnel`. This page uses its own simulation for now. If the backend endpoint lands, the funnel can be switched to it; note its stages differ (`scanned → answered_5plus → signed_up → redeemed`).

## Known deviations from the repo rules

Kept on purpose for the demo (CWT96's call): the page shows **Arsenal vs Chelsea** with their crests (`assets/`), not the fictional clubs in `data/source/`, and uses the **Matchday Call** name from the phone design rather than Quick-Play.
