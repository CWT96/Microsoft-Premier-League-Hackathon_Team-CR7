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
| `&seconds=16` | Replay length in seconds |
| `&demo=0` | Hide the "Simulated data" tag |

## What is on the page

A light, report-style layout: the audience is a club or league marketing team, so clarity beats show. Premier League purple is kept for the top bar and the main data line; green only marks sign-ups (the goal metric); pink only marks goals.

- **Key numbers:** QR scans, players, peak players online, calls made (and how many calls opened), sign-ups (with conversion and share new to a Premier League account). They follow the replay.
- **Engagement during the match:** players online or calls per minute (toggle), with goals, half time and full time marked. Below it, on the same time axis, **sign-ups per minute**: near zero during play, a peak in the half-time save window, and a bigger spike after full time.
- **Key findings:** four one-line takeaways computed from the data, including how many half-time sign-ups left before full time.
- **Funnel table:** scanned → started → made a call → made a forecast → saw a sign-up prompt (half-time or full-time) → signed up, with share of scans and step conversion.
- **Channels table:** scans, sign-ups and sign-up rate per QR channel, sorted by rate, with a total row.
- **Replay match:** plays the match data back smoothly at a constant speed, with no pauses, for the demo video.

## Data (simulated)

**Match facts come from the same match the fan app plays.** `sim/simulate.js` reads `data/source/events/9000001.json` (`nbr-cal-seed42`, shown as Arsenal 2–1 Chelsea, names from `backend/demo/names.py`):

- the minute timeline, including stoppage time (first half to 45+2, second half to 90+4);
- the goals and scorers: Saka 23', João Pedro 60', Saliba 79';
- how many live calls can open each minute, using the fan app's rules from `backend/demo/calls.py` (a call opens on a completed pass, locks on the receiver's pass, shot or dribble, needs a window of at least 3 s, and calls are at least 6 s apart; shots first, then dribbles, then passes). That gives 347 calls over the match.

**User behaviour is simulated** by a seeded per-user model: each simulated person has a scan time and channel, may start a game, answers some of the calls that open while they are online (more right after a goal), makes forecasts, may leave early, and may sign up. Sign-ups come from two moments in the phone flow: the **half-time save window** (a sheet opens at the break for everyone who played some of the first half; "Maybe later" leaves a chip that a few people tap during the second half) and the **full-time "save your points" screen**. Anyone who saved at half-time is not counted again at full time.

```bash
node frontend/ops-dashboard/sim/simulate.js
```

This rewrites, deterministically (same seed, same output):

| File | What |
|---|---|
| `data/data.js` | Aggregates the page loads (`window.SIM`) |
| `data/matchday_call_minute_by_minute.csv` | One row per match minute: scans, games started, players online, calls opened, calls answered, sign-ups |
| `data/matchday_call_users.csv` | One row per simulated person (20,097 rows) |

The project rule is that user data is generated in `backend/social/` and served at `GET /api/ops/funnel`. This page uses its own simulation for now. If the backend endpoint lands, the funnel can be switched to it; note its stages differ (`scanned → answered_5plus → signed_up → redeemed`).

## Notes

- Same relabel as the fan app (approved in `CLAUDE.md`): the synthetic match is shown as Arsenal vs Chelsea with real player names and crests (`assets/`). The data stays synthetic.
- `backend/social/funnel.py` (`GET /api/ops/funnel`) belongs to the earlier Room prototype. This page does not use it; its stages also differ (`scanned → answered_5plus → signed_up → redeemed`).
- Not served by the deployed app yet: the Dockerfile copies only `frontend/fan-app/`. Open `index.html` locally.
