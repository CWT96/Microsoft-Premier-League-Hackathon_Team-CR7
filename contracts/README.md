# contracts/: WebSocket and REST contract

**Owners:** all three. This folder plus the `*.schema.json` files are the only agreement between `backend/` and `frontend/`. Change them through a PR that is approved by one backend owner **and** the frontend owner. Freeze target: **10/25 morning**. After that, additive changes only (new optional fields).

| File | What it defines |
|---|---|
| `event.schema.json` | One synthetic match event (`match.event`) |
| `question.schema.json` | A binary question (`question.open`) |
| `result.schema.json` | A settled question, per user (`question.result`) |
| `score.schema.json` | Final score card (`score.final`, `GET /api/score/{user_id}`) |

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
| `answer` | `{qid, option}` | Rejected if it arrives after `lock_at`. One answer per question. |
| `hint.request` | `{qid}` | Reveals the AI hint; halves points for this question. |
| `control` | `{action: "speed" \| "skip" \| "resume", value}` | Demo controls, accepted **only from the room host** (the room creator; a solo player is host of a private room). `speed`: 1–15. `skip`: `"next_dull"`, `"HT"`, `"FT"`. `resume`: leave the HT pause. |

### Server → client

| `type` | `data` | When |
|---|---|---|
| `session` | `{user_id, match_id, room_id, is_host, friends: [names]}` | Reply to `join`. Each room (incl. a solo private room) runs its own replay. |
| `match.event` | `MatchEvent` | Every event, for the 2D pitch |
| `match.state` | `{t, half, home, away, score: {home, away}, mode, dull_score, speed}` | Once per match second. `home`/`away` are team codes. `mode` ∈ `lobby`, `live`, `game`, `attack`, `break`, `full_time`. The replay runs at 1× while an instant question is open. |
| `question.open` | `Question` | Timing agent fires |
| `hint` | `{qid, ai_pick, p}` | Reply to `hint.request`; `p` in 0–1 |
| `question.locked` | `{qid}` | At `lock_at` |
| `question.result` | `QuestionResult` | Resolver settles the question (sent per user) |
| `catchup.card` | `{card_id, t_start, t_end, event_ids, headline, explanation, facts}` | After an attack ends. Same for the whole room; the UI badges events by the viewer's favourite player. |
| `score.final` | `FinalScore` | At full time |
| `leaderboard` | `{scope: "room" \| "friends" \| "global", entries: [{display_name, final_score, is_you}]}` | At full time, one message per scope (top 10 + you) |

During the match the UI shows only ✓/coin animations. It never shows point numbers or a running total.

**Answer windows:** instant questions lock 6 s after `open_at` (6 real seconds, since the replay is at 1×). Kickoff and half-time stacks lock after 60 real seconds, or when the host sends `resume`/`skip`. When `mode` turns `attack`, any open card is hidden and answers close immediately; the result still arrives later.

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
 "rank": {"room": 2, "global": 1840, "friends": 1},
 "summary": "You called 3 of 4 questions about Ward, your goalkeeper."}
```

## REST

| Method | Path | Returns |
|---|---|---|
| GET | `/api/matches` | `[{match_id, home, away, kickoff_label}]` |
| GET | `/api/matches/{match_id}/players` | `[{player_id, name, team, position}]`, position ∈ GK, DF, MF, FW |
| POST | `/api/rooms` | `{room_id, join_url}`. Frontend renders `join_url` as a QR code. |
| POST | `/api/signup` | body `{user_id, email}` → `{member_id, card_url}`. **Mock: nothing is sent or stored outside memory.** |
| GET | `/api/card/{member_id}` | `{member_id, display_name, final_score, qr_payload, tier}` for the web wallet card. `tier`: Bronze < 400, Silver 400–699, Gold ≥ 700 |
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
