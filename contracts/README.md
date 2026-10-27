# contracts/: WebSocket and REST contract

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
| `question.open` | `Question` | **Sent per user**, when that user's pacer picks up a timing moment. Users move through one shared stream of moments at their own pace; `qid` is the moment id, so two users answering the same moment see the same question. Exception: player moments differ per user and use `qid = <moment id>-<player_id>`. `open_at`/`lock_at` describe the **moment**, in match seconds, not the user's window: a user who picks a moment up late gets less time. The client counts down to `lock_at` against `match.state.t`; never assume `lock_at − open_at = 8`. A pacer only delivers a moment with at least 5 s left, and never after it has locked. `situation` uses the **moment vocabulary** (`question.schema.json`), not the detector vocabulary of `match.state`. |
| `nudge` | `Nudge` | To one user, after 2 cards in a row locked unanswered. No new cards until the user sends `feed.resume` (tapping the nudge). |
| `hint` | `{qid, ai_pick, p}` | Reply to `hint.request`; `p` in 0–1 |
| `question.locked` | `{qid}` | At `lock_at`, or earlier: when an attack starts, or when a set piece is taken for `lock_rule: on_restart` |
| `question.result` | `QuestionResult` | Resolver settles the question (sent per user) |
| `catchup.card` | `{card_id, t_start, t_end, event_ids, headline, explanation, facts}` | After an attack ends. Same for the whole room; the UI badges events by the viewer's favourite player. |
| `score.final` | `FinalScore` | At full time |
| `leaderboard` | `{scope: "room" \| "friends" \| "global", entries: [{display_name, final_score, is_you}]}` | At full time, one message per scope (top 10 + you) |

During the match the UI shows only ✓/coin animations. It never shows point numbers or a running total.

**Answer windows:** instant questions lock 8 s after `open_at` (8 real seconds, since the replay is at 1× in calm play). An unanswered card is replaced by the user's next card at lock. Kickoff and half-time stacks lock after 60 real seconds, water-break cards after 30, or when the host sends `resume`/`skip`. When a fast attack starts, any open card is hidden and answers close immediately; the result still arrives later.

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
