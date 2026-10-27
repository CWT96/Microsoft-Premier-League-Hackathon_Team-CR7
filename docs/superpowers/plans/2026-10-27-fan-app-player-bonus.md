# Fan App — Back Up to 2 Players (Player Bonus) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Team-doc item 7 / spec decision 5: on the pick screen the fan backs up to 2 players (starters or bench) from their side; each earns a passive bonus from what they actually did in the match, scored by position from the design table. Shown at half time (first half) and full time (whole match), added to the same total.

**Architecture:** `backend/demo/names.py` gains the design squads (position GK/DEF/MID/FWD, starter/bench). New pure `backend/demo/bonus.py` counts per-player stats per half from the raw StatsBomb events (same raw file `calls.py` reads) and scores them with the design table. `router.py` serves `GET /demo/squads`; `start` accepts `players`; `session.py` adds the first-half bonus before `demo.half_time` and the second-half bonus before `demo.full_time`. Frontend: player list on the pick screen; bonus lines on the half-time sheet and in the full-time "PLAYER BONUS" row.

**Tech Stack:** Python 3.11, pytest; vanilla JS.

**Spec:** `docs/superpowers/specs/2026-10-27-fan-app-demo-design.md` decision 5 ("Back up to 2 players from the matchday squad; they earn a passive bonus from how they actually play. No player questions."); team doc item 7 ("最多选 2 名；按设计稿里各位置的得分项从原始数据统计；没上场 0 分；终场给出每人的加分明细") and item 4 (squad with numbers and starter flag; bench selectable). Design: `git show origin/frontend/demo-ux:frontend/demo-ux/screens/Pick.dc.html` (squads `m1-home`/`m1-away`, `scoring()`, popular pick, copy, layout).

## Global Constraints

- Squads (exact, from the design `squads()`), mapped to our ids via `names.PLAYERS` by name:
  - Arsenal XI: Raya GK, White DEF, Saliba DEF, Gabriel DEF, Calafiori DEF, Rice MID, Zubimendi MID, Ødegaard MID, Saka FWD, Gyökeres FWD, Eze FWD; bench: Bruno Guimarães MID, Havertz FWD, Martinelli FWD, Merino MID, Timber DEF. Most backed: Saka.
  - Chelsea XI: Sánchez GK, James DEF, Fofana DEF, Colwill DEF, Hato DEF, Caicedo MID, Enzo MID, Palmer MID, Neto FWD, João Pedro FWD, Estêvão FWD; bench: Delap FWD, Gittens FWD, Lavia MID, Gusto DEF. Most backed: Palmer.
- Scoring table (exact, design `scoring()`): GK "Goalkeeper", earns "saves, clean sheets": Penalty save 400, Clean sheet 300, Save 40, Claimed cross 10. DEF "Defender", "tackles, clean sheets": Goal 400, Clean sheet 200, Blocked shot 30, Tackle won 20, Interception 20, Clearance 10. MID "Midfielder", "assists, key passes": Goal 350, Assist 200, Shot on target 50, Key pass 40, Tackle won 20, Interception 20. FWD "Forward", "goals, shots": Goal 300, Assist 200, Big chance made 60, Shot on target 50, Dribble past 20, Won a foul 10.
- Stat definitions from raw events (player = event `player.id`; per period): Goal = `Shot` with `shot.outcome.name == "Goal"`; Assist = `Pass` with `pass.goal_assist` true; Key pass = `Pass` with `pass.shot_assist` true; Big chance made = key pass whose assisted shot (`pass.assisted_shot_id`) has `shot.statsbomb_xg >= 0.2` (if xG absent in the data, count every key pass and report it); Shot on target = `Shot` with outcome `Saved` or `Saved To Post` (goals are counted as Goal, not also on target); Dribble past = `Dribble` with `dribble.outcome.name == "Complete"`; Won a foul = `Foul Won`; Tackle won = `Duel` with `duel.type.name == "Tackle"` and outcome in {Won, Success, Success In Play, Success Out}; Interception = `Interception` with outcome in {Won, Success, Success In Play, Success Out}; Clearance = `Clearance`; Blocked shot = `Block`; Save = `Goal Keeper` with `goalkeeper.type.name == "Shot Saved"`; Penalty save = `goalkeeper.type.name == "Penalty Saved"`; Claimed cross = `goalkeeper.type.name == "Collected"`; Clean sheet = the player's team conceded no goal in the whole match and the player started (FT only; none in this match — both teams scored). A stat whose raw field doesn't exist counts 0 (report which). Players who never played get 0.
- Bonus timing: first-half bonus (period 1 stats) is added to the total right before `demo.half_time`; the rest (period 2 stats + clean sheet) right before `demo.full_time`. Forecast/call rows unchanged.
- Wire (additive): `GET /demo/squads` → `{"teams": {"ARS": {"name", "popular": id, "players": [{id, name, num, pos, starter}]}, "CHE": {...}}, "scoring": {"GK": {"label", "earns", "rules": [[what, pts], ...]}, ...}}`. `start.data.players`: list of ≤ 2 ids from the chosen side (unknown/other-side ids dropped, duplicates removed, extras beyond 2 dropped). `demo.session.data.you.players`: `[{id, name, num, pos}]`. `demo.half_time.data.players`: `[{id, name, num, pos, points, stats: [{what, count, pts}]}]` (first half; only stats with count > 0) and `breakdown.players: int`. `demo.full_time.data.players`: same for the whole match; the `pb` row: `points` = whole-match bonus, `detail` = `"Earned by the {n} player(s) you backed"` (n ≥ 1) else `"Back players next match"`.
- Friends and crowd don't back players (their totals unchanged).
- Frontend: after the team is chosen, show the squad (STARTING XI, BENCH; number, name, position label) with up-to-2 toggles, "Most backed: {name}" quick pick, the position "earns from" intro, and cards for selected players listing their scoring rules; summary "Playing as {team} · backing {A} & {B}" or "· no players backed yet"; START PLAYING works with 0–2 players. Half-time sheet: a line per backed player "{name}'s bonus +{points}". Full time: under the PLAYER BONUS row, one line per player: "{num} {name} · {pos label}" + stats text like "Goal +300 · 2 shots on target +100" + "+{points}". HUD: small "Backing Saka & Rice" under the total instead of "Backing Arsenal" when players are backed.
- Do not modify `main.py`, `ingest/`, `llm.py`, `replay.py`, `bank/`, `resolve/`, `scoring/`, `social/`, non-demo tests.
- Commits `[area] Verb thing: description` + blank line + `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Branch `backend/demo-live-calls`.

## Review Focus

1. **Bad `players` in start** — ids from the other team, unknown, > 2, duplicates, non-list → sanitised, never a crash or close (Task 1 test).
2. **Bonus added once** — total at HT includes the first-half bonus, FT adds only the second-half part; rows/players sum matches (Task 1 test).
3. **Saka's real numbers** — Saka (FWD) has Goal +300 in the first half; Saliba (DEF) Goal +400 in the second half (Task 1 test).
4. **Pick screen with 0 players** — START PLAYING still works; full-time row says "Back players next match" (Task 2).

---

### Task 1: Squads, stats and bonus (backend)
**Files:** `backend/demo/names.py` (squad table), create `backend/demo/bonus.py`, `backend/tests/test_demo_bonus.py`; modify `session.py`, `router.py`, `script.py` only if needed.
- [ ] Tests first (Review Focus 1–3; `/demo/squads` shape; per-player stat counts for Saka, Saliba, Raya printed in the report), implement, full suite + demo files 3×, commit `[backend] Add demo player bonus: squads, per-position stats from raw events`.

### Task 2: Pick players and show the bonus (frontend)
**Files:** `frontend/fan-app/index.html`, `app.js`, `styles.css`.
- [ ] Implement per constraints and the Pick design; fetch `/demo/squads` on the pick screen (fallback: team-only pick if the fetch fails); send `players` in `start`; HT lines; FT player lines; HUD backing line; Play again keeps the same team and players. `node --check`, static tests; commit `[frontend] Add fan app player picks and player bonus breakdown`.

### Task 3: Deploy
- [ ] Build/push `matchday-call:v9`, update, live scripted run with players [Saka, Rice], Chrome check of the pick screen, push, PR comment.
