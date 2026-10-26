# frontend/demo-ux/

**Owner:** `CWT96`. **Tier:** design reference for P0–P2 screens.

This is the UX prototype for the phone second screen, built in a Claude design canvas: six phone screens (390×844) plus one spec board for the score HUD. It shows the flow, the copy, the motion and the points model. It is a design spec, not the production frontend. The real app is still built against `../../contracts/`.

## What's in here

| Path | What |
|---|---|
| `screens/Main.dc.html` | 01 Landing after scanning the QR code: live match card, "Play this match", room code |
| `screens/Pick.dc.html` | 02 Pick a team (live or upcoming match), then back up to 2 players from that team's matchday squad |
| `screens/Live.dc.html` | 03 Live, real-time calls: moving 2D pitch, AI call card (pass / shoot / dribble), points pop-up |
| `screens/Forecast.dc.html` | 04 Live, forecasts: next 5, 10 or 15 minutes, full-time result with a linked exact score |
| `screens/Result.dc.html` | 05 Full time: points count up, badges, room leaderboard, "Save my points" |
| `screens/Signup.dc.html` | 06 Email-only "semi sign-up", then a +500 welcome bonus |
| `screens/HUD.dc.html` | Score HUD spec: feedback tiers, points table, motion timing for one score event |
| `assets/crests/*.svg` | Club crests used by the screens (cleaned SVGs: no scripts or styles) |
| `canvas.json` | Canvas layout: artboard positions and order |

### How to read the `.dc.html` files

Each file is one screen, with two parts:
- **Markup:** HTML with inline styles. `{{name}}` is a value hole, `<sc-for>` is a loop and `<sc-if>` is a condition.
- **Logic:** the `<script type="text/x-dc">` block at the bottom: a small class with `state` and `renderVals()`. This is where the simulation, timers and scoring live.

The files need the design canvas runtime (`support.js`, not included), so they won't run if you open them directly. To click through the prototype, ask CWT96 for the canvas share link. To build the real thing, treat these files as the spec for layout, copy, colours and behaviour.

## The flow

1. **Scan → Landing.** No sign-up needed to start. The user can join a friend's room with a code.
2. **Pick.** Choose a team. Optionally back up to 2 players from that team's matchday squad, grouped by starting XI and bench.
   - Backing a player is **not a prediction**. The players earn bonus points from how they actually play, and the user does nothing.
   - The points depend on position (see below). Each picked player's card shows how they earn.
3. **Live, two modes** (tabs):
   - **Real time:** the AI opens a call only at a decision moment (free kick, player on the ball at the edge of the box, one-on-one). The user picks one of PASS, SHOOT or DRIBBLE.
     - **There is no countdown.** The call locks the moment the ball moves. A slow user sees "Too quick!", loses nothing and keeps the streak.
     - **Calls run themselves.** They open and close as the match goes on, and there is no "next" button.
   - **Forecast:** windows for the next 5, 10 and 15 minutes, plus a full-time result. The result and the exact score are linked: change one and the other updates. The exact score can't be lower than the live score.
4. **Full time.**
   - **Count-up:** "Count my points" starts it. Rows go real-time, then forecast, then player bonus. Each row counts up over 2.8 s with the same slow-down curve, and every bar fills to 100%.
   - **After the count:** the rank stamp, badges and room leaderboard come in one at a time, about 3.5 s in total.
5. **Save points.** Email only, no password (we send a sign-in link). The screen offers the season leaderboard and partner rewards, then gives a +500 welcome bonus.

## Points (as drawn in the prototype)

**Never minus.** A wrong call still earns +10.

| Moment | Points |
|---|---|
| Any real-time call | +10 |
| Correct real-time call | +100 |
| Streak of 3+ correct calls | +50 per step |
| Forecast for the next 5 / 10 / 15 minutes | +500 / +1,000 / +1,500 |
| Full-time result (+ exact score) | +3,000 (+2,000) |
| Welcome bonus on sign-up | +500 |

### Player bonus, by position

| Position | Points |
|---|---|
| **GK** | penalty save +400, clean sheet +300, save +40, claimed cross +10 |
| **DEF** | goal +400, clean sheet +200, blocked shot +30, tackle won +20, interception +20, clearance +10 |
| **MID** | goal +350, assist +200, shot on target +50, key pass +40, tackle won +20, interception +20 |
| **FWD** | goal +300, assist +200, big chance made +60, shot on target +50, dribble past +20, won a foul +10 |

## Live pitch simulation (`Live.dc.html`)

- **Teams:** 22 numbered players. Arsenal play 4-3-3, attacking right. Chelsea play 4-2-3-1.
- **Movement:** every 400 ms, each player runs toward a target. Speed is capped at about 4 m/s jogging and 8 m/s sprinting, so nobody teleports.
- **Team shape:** both teams shift with the ball.
- **Before a call:** about 4 s before it opens, the ball is passed to the player in the call, and he carries it into position.
- **Outcomes:** a pass flies to a teammate, a shot goes into the keeper's hands, and a dribble beats the defender.
- **Screen sync chip:** the user picks TV (about 6 s behind), streaming (about 30 s behind) or stadium. Calls are held back by the same delay, so the app never runs ahead of their screen.

In the real app, these positions come from `match.event` / `match.state`, not from this simulation.

## Visual system

- **Colours** follow the Premier League palette: `#1A0020` background, `#37003C` cards, `#00FF85` points, `#04F5FF` live/selected, `#FF5C93` LIVE, `#C9A2FF` player bonus.
- **Type:** Barlow Condensed for display, Inter for text.
- **Motion:** feedback 150–250 ms, entrances 300–420 ms with a slow-down curve `cubic-bezier(0.05, 0.7, 0.1, 1)`. One thing moves at a time, and siblings start 90–140 ms apart.

## Differences from `docs/pipeline.md` (to agree as a team)

The prototype explores a different direction on a few points. These need a decision before the frontend is built against `contracts/`:
- **Number of options:** real-time calls have **3 options** (pass / shoot / dribble). The pipeline says exactly 2.
- **Points during the match:** points and a running total **are shown** during the match (Battlefield-style pop-ups). The pipeline says only ✓ and coins until full time.
- **Points scale:** points are raw and uncapped. The pipeline uses a 0–1000 score card.
- **Favourite player:** backed players earn a passive bonus from real stats. In the pipeline they get their own questions.
- **Real-time timing:** calls lock on the next touch of the ball. The pipeline uses a 6 s countdown.
- **Real data:** the prototype uses real clubs, players and crests. The repo rule is synthetic data and fictional clubs only.
