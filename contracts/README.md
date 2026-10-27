# contracts/: WebSocket and REST contract

> **Judged demo:** the Matchday Call fan app (served at `/`) uses `WS /demo/ws` and `GET /demo/squads`, documented in [Demo mode](#demo-mode-demows-and-demosquads) below. The original yes/no Room flow (`/ws`, `/api/*`) described in the other sections still exists unchanged.

**Owners:** all three. This folder plus the `*.schema.json` files are the only agreement between `backend/` and `frontend/`. Change them through a PR that is approved by one backend owner **and** the frontend owner. Freeze target: **10/25 morning**. After that, additive changes only (new optional fields).

| File | What it defines |
|---|---|
| `event.schema.json` | One synthetic match event (`match.event`) |
| `question.schema.json` | A binary question (`question.open`) |
| `result.schema.json` | A settled question, per user (`question.result`) |
| `score.schema.json` | Final score card (`score.final`, `GET /api/score/{user_id}`) |
| `state.schema.json` | Match state once per second (`match.state`) |
| `tracking.schema.json` | Player and ball positions once per second (`match.tracking`) |
| `timing_decision.schema.json` | Why the timing agent opened, held or blocked a moment (`timing.decision`) |
| `nudge.schema.json` | Gentle prompt when a user's feed pauses (`nudge`) |

Examples below marked `<!-- schema: X -->` are validated against `X.schema.json` (see "Validate" at the end).

## WebSocket

Endpoint: `GET /ws?match_id=<id>&room_id=<id|empty>`

Every message, in both directions, uses one envelope:

```
{"type": "question.open", "data": { ... }}
```

### Client → server

| `type` | `data` | Notes |
|---|---|---|
| `join` | `{match_id, room_id, display_name, team, player_id}` | First message after connect. `room_id` may be null (global only). |
| `answer` | `{qid, option}` | Rejected if it arrives after `lock_at` or after `question.locked`, whichever comes first. One answer per question. |
| `hint.request` | `{qid}` | Reveals the AI hint; halves points for this question. |
| `control` | `{action: "speed" \| "skip" \| "resume", value}` | Demo controls, accepted **only from the room host** (the room creator; a solo player is host of a private room). `speed`: 1–15. `skip`: `"next"` only: jumps to 3 s before the next scheduled moment (player slot, water break, half time, full time), never past one. Calm play resumes within seconds after every jump, so a judge still sees the feed. `resume`: leave a half-time or water-break pause. |
| `feed.resume` | `{}` | The user tapped the nudge; their feed restarts. |

### Server → client

| `type` | `data` | When |
|---|---|---|
| `session` | `{user_id, match_id, room_id, is_host, friends: [names]}` | Reply to `join`. Each room (incl. a solo private room) runs its own replay. |
| `match.event` | `MatchEvent` | Every event, for the 2D pitch |
| `match.state` | `MatchState` (`state.schema.json`) | **Per user**, once per match second (and once per real second, with the same `t`, while the replay is paused). `home`/`away` are team codes. `mode` ∈ `lobby`, `live`, `game`, `attack`, `break`, `full_time`; `game` means this user has an open card. `break_kind` tells half time from a water break. `cost`, `movement`, `pressure_m`, `situation` feed the director view. The replay runs at 1× in calm play. |
| `match.tracking` | `TrackingFrame` | Once per match second. **Fixed frame:** the home team attacks left to right for the whole match. Event coordinates are per acting team (see `event.schema.json`); to draw an away-team event in the fixed frame use (105 − x, 68 − y). |
| `timing.decision` | `TimingDecision` | At each timing decision point and each calm/silent transition (not every second). For the director view. |
| `question.open` | `Question` | **Sent per user**, when that user's pacer picks up a timing moment. Users move through one shared stream of moments at their own pace; `qid` is the moment id, so two users answering the same moment see the same question. Exception: player moments differ per user and use `qid = <moment id>-<player_id>`. `open_at`/`lock_at` describe the **moment**, in match seconds, not the user's window: a user who picks a moment up late gets less time. The client counts down to `lock_at` against `match.state.t`; never assume `lock_at − open_at = 6`. A pacer only delivers a moment with at least 3 s left, and never after it has locked. `situation` uses the **moment vocabulary** (`question.schema.json`), not the detector vocabulary of `match.state`. |
| `nudge` | `Nudge` | To one user, after 2 cards in a row locked unanswered. No new cards until the user sends `feed.resume` (tapping the nudge). |
| `hint` | `{qid, ai_pick, p}` | Reply to `hint.request`; `p` in 0–1 |
| `question.locked` | `{qid}` | At `lock_at`, or earlier: when an attack starts, or when a set piece is taken for `lock_rule: on_restart` |
| `question.result` | `QuestionResult` | Resolver settles the question (sent per user) |
| `catchup.card` | `{card_id, t_start, t_end, event_ids, headline, explanation, facts}` | After an attack ends. Same for the whole room; the UI badges events by the viewer's favourite player. |
| `score.final` | `FinalScore` | At full time |
| `leaderboard` | `{scope: "room" \| "friends" \| "global", entries: [{display_name, final_score, is_you}]}` | At full time, one message per scope (top 10 + you) |

During the match the UI shows only ✓/coin animations. It never shows point numbers or a running total.

**Answer windows:** instant questions lock 6 s after `open_at` (6 real seconds, since the replay is at 1× in calm play). An unanswered card is replaced by the user's next card at lock. If play settles a question while its card is up, `question.locked` arrives early; a user who had not answered then gets `question.result` with `you.answered: false` (the frontend's "too quick" case). Kickoff and half-time stacks lock after 60 real seconds, water-break cards after 30, or when the host sends `resume`/`skip`. When a fast attack starts, any open card is hidden and answers close immediately; the result still arrives later.

**Time:** `t` is the displayed match clock. The second half starts at t = 2700 even after first-half stoppage time, so order events by (`half`, `t`). In first-half stoppage time `t` runs past 2700 (e.g. 2760 = 45+1:00). `t` stops during half time. During a water break `t` keeps running, as in real football, but the replay first pauses at the break's start until the host resumes or 30 real seconds pass. Match seconds equal real seconds only at 1×.

### Examples

<!-- schema: event -->
```json
{"id": "e1042", "t": 2066, "half": 1, "team": "NBR", "player_id": "nbr-8-okafor",
 "type": "pass", "start": [52, 30], "end": [68, 22], "outcome": "complete"}
```

<!-- schema: question -->
```json
{"qid": "q-17", "category": "instant", "template_id": "next_pass_dir",
 "text": "Northbridge are passing it around midfield. Next pass: forward or back?",
 "options": [{"id": "forward", "label": "⬆ Forward"}, {"id": "backward", "label": "⬇ Back"}],
 "open_at": 2058, "lock_at": 2064, "resolve_by": 2079, "hint_available": true,
 "player_id": null, "params": {"dead_zone_m": 5}}
```

<!-- schema: question -->
```json
{"qid": "q-21", "category": "player", "template_id": "player_touch_window",
 "text": "Will Ward touch the ball in the next 3 minutes?",
 "options": [{"id": "yes", "label": "Yes"}, {"id": "no", "label": "No"}],
 "open_at": 2400, "lock_at": 2406, "resolve_by": 2586, "hint_available": true,
 "player_id": "cal-1-ward", "params": {"window_s": 180}}
```

<!-- schema: question -->
```json
{"qid": "q-30", "category": "instant", "template_id": "keep_ball",
 "text": "Will they keep the ball for 15s?",
 "options": [{"id": "yes", "label": "Yes"}, {"id": "no", "label": "No"}],
 "open_at": 1312, "lock_at": 1318, "resolve_by": 1333, "hint_available": true,
 "player_id": null, "params": {}, "tone": "Corner coming. Quick one!",
 "situation": "set_piece_wait", "lock_rule": "on_restart"}
```

<!-- schema: event -->
```json
{"id": "e0310", "t": 1310, "half": 1, "team": "NBR", "player_id": null, "type": "corner",
 "start": [105, 68], "outcome": null}
```

<!-- schema: state -->
```json
{"t": 2058, "half": 1, "home": "NBR", "away": "CAL", "score": {"home": 0, "away": 0},
 "mode": "game", "break_kind": null, "speed": 1, "dull_score": 0.82, "cost": 0.18,
 "movement": 0.12, "pressure_m": 6.4, "situation": "open_play"}
```

<!-- schema: tracking -->
```json
{"t": 2058, "half": 1, "ball": [52.0, 30.0],
 "players": [{"player_id": "nbr-8-okafor", "team": "NBR", "x": 52.0, "y": 30.5},
             {"player_id": "cal-1-ward", "team": "CAL", "x": 101.5, "y": 34.0}]}
```

<!-- schema: timing_decision -->
```json
{"t": 2058, "half": 1, "action": "open", "source": "rule", "moment_ids": ["q-17"],
 "situation": "open_play", "tone": "Quiet spell, quick one!",
 "reason": "Ball deep in midfield, players barely moving, no shot for 6 minutes.", "blocked_by": null,
 "inputs": {"cost": 0.18, "ball_zone": "middle_third", "threat": 0.04, "movement": 0.12,
            "pressure": 0.2, "shot_gap": 1.0, "context": 0.3, "calm_for_s": 14}}
```

<!-- schema: timing_decision -->
```json
{"t": 2071, "half": 1, "action": "silent", "source": "rule", "moment_ids": [],
 "situation": "attack", "tone": null, "reason": "Fast attack: ball in the final third and players sprinting.",
 "blocked_by": null, "inputs": {"cost": 0.95, "calm_for_s": 0}}
```

<!-- schema: nudge -->
```json
{"text": "Still with us? Tap for the next one 👋"}
```

<!-- schema: result -->
```json
{"qid": "q-17", "status": "resolved", "correct_option": "forward",
 "message": "Forward! 16m upfield.",
 "explanation": "Okafor spotted a gap between Calder's midfield lines and played it 16m upfield. Only 27% of players called it.",
 "new_term": "line-breaking pass", "evidence": ["e1042"],
 "facts": {"dx_m": 16, "crowd_forward": 27},
 "crowd_split": {"global": {"forward": 27, "backward": 73}, "room": {"forward": 50, "backward": 50},
                 "friends": {"forward": 33, "backward": 67}},
 "you": {"answered": true, "option": "forward", "correct": true, "used_hint": false, "streak": 2}}
```

<!-- schema: result -->
```json
{"qid": "q-18", "status": "void", "correct_option": null,
 "message": "Plot twist, he went sideways! Nobody saw that coming 😄",
 "explanation": null, "new_term": null, "evidence": ["e1051"], "facts": {},
 "crowd_split": {"global": {"forward": 41, "backward": 59}},
 "you": {"answered": true, "option": "backward", "correct": null, "used_hint": false, "streak": 2}}
```

<!-- schema: score -->
```json
{"user_id": "u-alex", "match_id": "nbr-cal-seed42", "final_score": 431,
 "issued": 22, "answered": 19, "correct": 13, "hit_rate": 0.68,
 "by_category": {"instant": {"issued": 14, "correct": 8, "points": 186},
                 "forecast": {"issued": 4, "correct": 2, "points": 104},
                 "player": {"issued": 4, "correct": 3, "points": 141}},
 "best_streak": 4, "hints_used": 2, "terms_learned": ["line-breaking pass", "switching play"],
 "tier": "Rising Star",
 "radar": {"crystal_ball": {"score": 70, "answered": 4, "correct": 2},
           "galaxy_brain": {"score": 82, "answered": 9, "correct": 6},
           "pause_pro": {"score": 64, "answered": 5, "correct": 3},
           "bestie_mode": {"score": 90, "answered": 4, "correct": 3},
           "lightning_thumbs": {"score": 75, "answered": 22, "correct": 13},
           "on_fire": {"score": 60, "answered": 22, "correct": 13}},
 "rank": {"room": 2, "global": 412, "friends": 1},
 "summary": "You called 3 of 4 questions about Ward, your goalkeeper."}
```

## REST

| Method | Path | Returns |
|---|---|---|
| GET | `/api/matches` | `[{match_id, home, away, home_name, away_name, kickoff_label}]` (`home`/`away` are team codes, `*_name` the display names) |
| GET | `/api/matches/{match_id}/players` | `[{player_id, name, team, position}]`, position ∈ GK, DF, MF, FW |
| POST | `/api/rooms` | `{room_id, join_url}`. Frontend renders `join_url` as a QR code. |
| POST | `/api/signup` | body `{user_id, email}` → `{member_id, card_url}`. **Mock: nothing is sent or stored outside memory.** |
| GET | `/api/card/{member_id}` | `{member_id, display_name, final_score, qr_payload, tier}` for the web wallet card. `tier`: Rookie < 400, Rising Star 400–699, Super Fan ≥ 700 |
| GET | `/api/score/{user_id}` | `FinalScore` |
| GET | `/api/ops/funnel?match_id=` | `{scanned, answered_5plus, signed_up, redeemed, new_members}`, simulated |
| GET | `/healthz` | `{"ok": true}` |

## Demo mode: /demo/ws and /demo/squads

This is the protocol of the judged Matchday Call fan app (`frontend/fan-app/`). Code: `backend/demo/router.py` (socket, close codes) and `backend/demo/session.py` (messages, scoring). One socket plays one fan's ~93 s highlights run of `nbr-cal-seed42`, shown as Arsenal (`ARS`, home) vs Chelsea (`CHE`, away). Every run is private to that socket; there is no shared room state. The examples below are real payloads from a local cache-mode run, trimmed with `…`. They are prose, not schema-validated.

Endpoint: `GET /demo/ws` (`wss://` on the deployed https site). Same envelope as `/ws`: `{"type": "...", "data": {...}}`. Frames over 4096 bytes or that are not JSON are skipped (before `start` they close the socket with 1008).

### Lifecycle and close codes

1. Client connects. If `MAX_DEMO_SESSIONS` sockets (default 50) are already open, the server accepts and closes at once with **1013** (try again later).
2. Client sends `start` within **10 s**. A missing, late or malformed `start`, or a team other than `ARS`/`CHE`, closes with **1008**. Bad `players` never close the socket; they are dropped.
3. Server sends `demo.session`, then the timed cues of the highlights run, ending with `demo.full_time`.
4. After `demo.full_time` the socket stays open for up to **120 s** (`POST_MATCH_S`) so the fan can still `signup`. Then the server closes with **1000**. It also closes with 1000 whenever the client leaves.

Clock values: `t` is match seconds (same convention as `/ws`), `clock` is the display label (`MM:SS`, or `45+M:SS` / `90+M:SS`), `score` is `[home, away]`. Pitch coordinates are metres in a fixed frame: Arsenal attack left to right (105 × 68), Chelsea's are rotated 180°.

### Client → server

| `type` | `data` | Reply |
|---|---|---|
| `start` | `{team: "ARS" \| "CHE", players?: [id, id]}` | `demo.session`. `players`: up to 2 ids from that team's squad in `GET /demo/squads`; unknown, other-team or duplicate ids are dropped. |
| `call.answer` | `{call_id, pick: "PASS" \| "SHOOT" \| "DRIBBLE"}` | None. Accepted only for the open call, once per call. Anything else is ignored silently. The result arrives in `call.result`. |
| `forecast.pick` | `{key: "f5" \| "f10" \| "f15", pick}` | `forecast.locked` or `forecast.rejected`. Picks: `ars`/`che` for `f5` and `f10`, `goal`/`nogoal` for `f15`. |
| `forecast.final` | `{result: "ars" \| "draw" \| "che", home, away}` | `forecast.final_saved` or `forecast.rejected` (`key: "final"`). Goals are integers 0–9, must agree with `result`, and cannot be below the current score. Can be changed until it locks at the start of the second half. |
| `signup` | `{email}` | `signup.saved` or `signup.rejected`. The email is only checked for shape; it is never stored, logged, sent or echoed. |

```json
{"type": "start", "data": {"team": "ARS", "players": ["910101", "910102"]}}
{"type": "call.answer", "data": {"call_id": "c-ed3e2846-9efa-4407-b2ed-cab03e3869e2", "pick": "PASS"}}
{"type": "forecast.pick", "data": {"key": "f5", "pick": "ars"}}
{"type": "forecast.final", "data": {"result": "ars", "home": 2, "away": 1}}
{"type": "signup", "data": {"email": "fan@example.com"}}
```

### Server → client: the run

| `type` | `data` | When |
|---|---|---|
| `demo.session` | `{teams: {home, away}, you: {team, players: [card]}, duration, ai_lines, forecasts}` | Once, right after `start`. `ai_lines` is true when every call line came from the LLM cache and passed the guards. `forecasts` lists the three windows and the final-result options with their points. |
| `demo.jump` | `{title, clock}` | A 2 s title card before each highlight clip (the first one is the intro). |
| `demo.clock` | `{half, clock, score, t}` | Once per real second while a clip plays. |
| `demo.event` | `{type, team, player, xy, end_xy}` | Each on-ball event in a clip. `type` ∈ `pass`, `carry`, `shot`, `goal`, `save`, `kickoff`, `dribble`. `xy`/`end_xy` may be null. |
| `demo.shape` | `{players: [{team, num, x, y}]}` | 22 dots at each clip start and with each located event. **Estimated** from the formation and the ball; there is no tracking data. |
| `demo.goal` | `{team, player, clock, score}` | Right after the goal's `demo.event`. |
| `call.open` | `{call_id, player, team, prompt, options}` | The ball is passed to a player. `prompt` is the AI-written (cached) setup line. |
| `call.locked` | `{call_id}` | That player passes, shoots or dribbles. Answers after this are ignored. |
| `call.result` | `{call_id, answer, pick, too_quick, correct, points, streak_bonus, total, streak, explain}` | Right after `call.locked`. Points: +10 answered, +100 more if correct, streak bonus +50 per step from the 3rd correct call in a row. Unanswered: `pick: null`, `too_quick: true`, 0 points, streak kept. |
| `demo.half_time` | `{score, seconds, room, breakdown, players, you: {rank}, total, saved}` | After the first half. `seconds` is the pause length (10). `players` is the bonus card of each backed player for the first half. |
| `demo.full_time` | `{score, seconds, rows, badges, stamp, room, players, total, saved}` | Last cue. `rows` are the three result lines, `badges` ⊆ ORACLE / ON FIRE / GOOD EYE, `stamp` ranks the fan in the room and against a simulated crowd of 1,000. |

`total` is the fan's running total in every message that carries it: calls, forecasts, player bonus (first half added before `demo.half_time`, the rest before `demo.full_time`) and the signup bonus. `room.players` is the fan plus three simulated friends (Mia, Dan, Leo), sorted by points.

```json
{"type": "demo.session", "data": {"teams": {"home": {"code": "ARS", "name": "Arsenal", "crest": "assets/ars.svg"}, "away": {"code": "CHE", "name": "Chelsea", "crest": "assets/che.svg"}}, "you": {"team": "ARS", "players": [{"id": "910101", "name": "Raya", "num": 1, "pos": "GK"}]}, "duration": 93.0, "ai_lines": true, "forecasts": {"windows": [{"key": "f5", "label": "NEXT 5 MIN", "minutes": 5, "points": 500, "question": "Who has more of the ball?", "options": [{"id": "ars", "label": "ARSENAL"}, {"id": "che", "label": "CHELSEA"}]}, "…f10, f15"], "final": {"points": 3000, "exact_points": 2000, "options": [{"id": "ars", "label": "ARSENAL"}, {"id": "draw", "label": "DRAW"}, {"id": "che", "label": "CHELSEA"}]}}}}
{"type": "demo.jump", "data": {"title": "Arsenal on the attack", "clock": "11:31"}}
{"type": "demo.clock", "data": {"half": 1, "clock": "11:31", "score": [0, 0], "t": 691.0}}
{"type": "demo.event", "data": {"type": "pass", "team": "ARS", "player": "Raya", "xy": [5.2, 25.6], "end_xy": [9.6, 23.8]}}
{"type": "demo.shape", "data": {"players": [{"team": "ARS", "num": 1, "x": 5.0, "y": 33.2}, {"team": "CHE", "num": 9, "x": 19.7, "y": 31.9}, "…20 more"]}}
{"type": "demo.goal", "data": {"team": "ARS", "player": "Saka", "clock": "23:54", "score": [1, 0]}}
{"type": "call.open", "data": {"call_id": "c-ed3e2846-9efa-4407-b2ed-cab03e3869e2", "player": "Gabriel", "team": "ARS", "prompt": "Gabriel has the ball in his own half for Arsenal. What will he do next?", "options": ["PASS", "SHOOT", "DRIBBLE"]}}
{"type": "call.locked", "data": {"call_id": "c-ed3e2846-9efa-4407-b2ed-cab03e3869e2"}}
{"type": "call.result", "data": {"call_id": "c-ed3e2846-9efa-4407-b2ed-cab03e3869e2", "answer": "PASS", "pick": "PASS", "too_quick": false, "correct": true, "points": 110, "streak_bonus": 0, "total": 110, "streak": 1, "explain": "Gabriel passed the ball to a teammate, helping Arsenal keep their move going."}}
{"type": "demo.half_time", "data": {"score": [1, 0], "seconds": 10, "room": {"code": "MXTZ", "players": [{"name": "You", "pts": 2370, "saved": false, "me": true}, {"name": "Mia", "pts": 1890, "saved": true, "me": false}, "…Leo, Dan"]}, "breakdown": {"calls": 140, "forecasts": 2000, "players": 230}, "players": [{"id": "910101", "name": "Raya", "num": 1, "pos": "GK", "points": 190, "stats": [{"what": "Save", "count": 4, "pts": 160}, {"what": "Claimed cross", "count": 3, "pts": 30}]}], "you": {"rank": 1}, "total": 2370, "saved": false}}
{"type": "demo.full_time", "data": {"score": [2, 1], "seconds": 0, "rows": [{"key": "rt", "label": "REAL-TIME CALLS", "points": 260, "detail": "2 correct calls · best streak of 1"}, {"key": "fc", "label": "FORECAST", "detail": "2 forecasts hit · full-time result nailed", "points": 7000}, {"key": "pb", "label": "PLAYER BONUS", "detail": "Earned by the 2 players you backed", "points": 400}], "badges": [{"title": "ORACLE", "text": "Called the full-time result"}, {"title": "GOOD EYE", "text": "You called João Pedro's goal"}], "stamp": "#1 IN ROOM MXTZ · TOP 19% TONIGHT", "room": {"code": "MXTZ", "players": ["…"]}, "players": ["…full-match bonus cards"], "total": 8160, "saved": true}}
```

### Server → client: forecasts and signup

| `type` | `data` | When |
|---|---|---|
| `forecast.locked` | `{key, pick, from_clock, to_clock}` | Reply to an accepted `forecast.pick`. The window runs in match time from the current clock for 5, 10 or 15 minutes, cut at the end of the half, including minutes the highlights skip. |
| `forecast.rejected` | `{key, reason}` | Reply to a refused pick. `reason`: `bad_pick` (unknown key/option or bad shape; `key` is null if not a string), `not_live` (no clip is playing: intro, jump card, half time, full time), `pending` (that window is already running), `locked` (final pick after it locked). |
| `forecast.result` | `{key, pick, outcome, correct, points, total, to_clock, text}` | Just before the `demo.clock`, `demo.half_time` or `demo.full_time` cue that passes `to_clock`. Settled from the real events: `f5` more passes (`outcome` may be `none` on a tie), `f10` first shot (`none` if no shot), `f15` any goal. Points 500 / 1,000 / 1,500 if right, else 0. |
| `forecast.final_saved` | `{result, home, away}` | Reply to an accepted `forecast.final`. |
| `forecast.final_locked` | `{result, home, away}`, or `{}` if no pick | At the first `demo.clock` of the second half. |
| `forecast.final_result` | `{result, home, away, final, result_points, exact_points, points, total}` | Just before `demo.full_time`, only if a final pick was saved. +3,000 for the right result, +2,000 more for the exact score. |
| `signup.saved` | `{bonus, total}` | Reply to the first valid `signup`: +500, once. |
| `signup.rejected` | `{reason}` | `bad_email` or `already`. |

```json
{"type": "forecast.locked", "data": {"key": "f5", "pick": "ars", "from_clock": "11:32", "to_clock": "16:32"}}
{"type": "forecast.rejected", "data": {"key": "f5", "reason": "pending"}}
{"type": "forecast.result", "data": {"key": "f5", "pick": "ars", "outcome": "ars", "correct": true, "points": 500, "total": 630, "to_clock": "16:32", "text": "Forecast hit"}}
{"type": "forecast.final_saved", "data": {"result": "ars", "home": 2, "away": 1}}
{"type": "forecast.final_locked", "data": {"result": "ars", "home": 2, "away": 1}}
{"type": "forecast.final_result", "data": {"result": "ars", "home": 2, "away": 1, "final": [2, 1], "result_points": 3000, "exact_points": 2000, "points": 5000, "total": 7990}}
{"type": "signup.saved", "data": {"bonus": 500, "total": 2870}}
{"type": "signup.rejected", "data": {"reason": "already"}}
```

### GET /demo/squads

Both squads and the player-bonus scoring table, for the "back up to 2 players" screen. `popular` is the id of the player the app offers as a one-tap quick pick; `pos` ∈ `GK`, `DEF`, `MID`, `FWD`; `rules` are `[stat, points]` pairs. Code: `backend/demo/bonus.py` (`squads`).

```json
{"teams": {"ARS": {"name": "Arsenal", "popular": "910109", "players": [{"id": "910101", "name": "Raya", "num": 1, "pos": "GK", "starter": true}, "…"]},
           "CHE": {"name": "Chelsea", "popular": "910210", "players": [{"id": "910201", "name": "Sánchez", "num": 1, "pos": "GK", "starter": true}, "…"]}},
 "scoring": {"GK": {"label": "Goalkeeper", "earns": "saves, clean sheets", "rules": [["Penalty save", 400], ["Clean sheet", 300], ["Save", 40], ["Claimed cross", 10]]},
             "DEF": {"…": "…"}, "MID": {"…": "…"}, "FWD": {"…": "…"}}}
```

## Validate

```bash
pip install jsonschema
python - <<'PY'
import json, re, pathlib, jsonschema
d = pathlib.Path("contracts")
txt = (d / "README.md").read_text()
for name, block in re.findall(r"<!-- schema: (\w+) -->\s*```json\n(.*?)```", txt, re.S):
    schema = json.loads((d / f"{name}.schema.json").read_text())
    jsonschema.validate(json.loads(block), schema)
    print("ok", name)
PY
```
