# Quick-Play Pipeline: Turning Dull Match Moments into a Game

**Goal:** attract people who don't usually watch football by starting quick prediction games ("Next pass: forward or backward?") during the dull stretches of a match, such as passing around midfield. Each game ends the moment the action heats up.

**Pitch line:** *We turn the boring 60% of a match into a game for people who don't watch football.*

**Core rule:** code decides what happened and who wins. LLMs only choose and phrase the question and explain the result.

## Constraints (from `source_reports/`)

- Submission deadline: **2026-10-27 23:59 PT**.
- **Synthetic data only.** The organizers provide no real match data.
- Deliverables: a public GitHub repo, something judges can test, English materials, and a **demo video under 2 minutes**.
- Judging is 5 criteria at 20% each: technical implementation, agent design, real-world impact, UX/demo, and category fit.

## Pipeline overview

```
[0] Synthetic match generator ──events──► Event bus (replay at 1×–4× speed)
                                             │
            ┌────────────────────────────────┼─────────────────────────┐
            ▼                                ▼                         ▼
[1] Dull-moment detector (code)     [4] Resolver (code)        Live match feed (UI)
            │ trigger                        │ result + evidence
            ▼                                ▼
[2] Question agent (LLM)            [5] Explainer agent (LLM + number guard)
            │ question                       │ 1–2 sentence explanation
            ▼                                ▼
[3] Probability model (Python)      [6] Scoring + user profile ──► back to [2]
            │ AI odds + points
            ▼
      Push to users ── answers locked at t_lock
```

---

## Stages

### [0] Synthetic match generator (Python)

- Simulates a match between two fictional clubs as a stream of events:

  ```json
  {"id":"e1042","t":"34:26","team":"NBR","player":"Okafor","type":"pass",
   "start":[52,30],"end":[71,22],"outcome":"complete"}
  ```

- Coordinates are in metres on a 105×68 pitch, normalised so each team attacks left to right.
- **Seeded**, so every replay is identical. That makes the demo reproducible and gives known answers for testing.
- **Plants dull stretches** (long midfield possessions) and a few sudden attacks on purpose, so we can measure whether the detector catches them.

### [1] Dull-moment detector (pure code)

Every second, it computes features over a rolling 25-second window:

| Feature | Dull when |
|---|---|
| Passes per minute | High, but… |
| Ball position | …the ball stays in the middle third (x between 35 and 70) |
| Max threat (xT) in the window | Below 0.05 |
| Time since the last shot | More than 3 minutes |
| Current possession length | More than 6 passes |

`dull_score` is a weighted sum between 0 and 1.

- **Enter game mode:** `dull_score > 0.7` holds for 8 seconds.
- **Exit immediately:** the ball enters the final third, xT rises above 0.15, or there's a shot, foul or card. The game must never cover a real chance.
- **Throttle:** at least 45 seconds between questions, and at most about 8 per half.

It's deterministic and can be tested against the planted dull stretches. Report the misses as well as the hits.

### [2] Question agent (LLM on Microsoft Foundry)

- **Input:** a snapshot of the match state plus the user's profile (level, accuracy per question type, language, concepts already learned).
- **It picks from a fixed catalog and never invents questions**, so the resolver can always settle the result:

| ID | Question | Options | Resolution rule | Window |
|---|---|---|---|---|
| `next_pass_dir` | Next pass: which way? | forward / sideways / backward | dx > +5m → forward, dx < −5m → backward, otherwise sideways | First pass after lock; void after 15s |
| `reach_final_third` | Will they reach the final third? | yes / no | Ball x > 70 while still in possession | 20s |
| `keep_ball` | Will they keep the ball for 15s? | yes / no | No turnover event | 15s |
| `shot_soon` | Shot in the next 60s? | yes / no | Any shot event | 60s |

- Beginners get `next_pass_dir` or `keep_ball` (fast and visual). Experienced users get `reach_final_third` or `shot_soon`.
- **Output** is strict JSON, validated against a schema. If validation fails, fall back to a default question:

  ```json
  {"question_id":"next_pass_dir","lock_in_s":6,"lang":"en",
   "text":"Northbridge are passing it around midfield. Next pass: which way?",
   "hint":"Backward passes are safe; forward passes are riskier but create chances."}
  ```

### [3] Probability model (Python, not an LLM)

- A simple model trained on the synthetic data, such as logistic regression or a frequency table. Features include zone, pressure (nearest opponent distance) and passes in the possession.
- It outputs a probability for each option. That probability becomes the "AI thinks…" display and the reward:

  ```
  points = 10 + round(90 × (1 − p_chosen))
  ```

  Calling an unlikely outcome correctly pays more.
- **Lock:** answers close at `t_lock` (6 seconds after the question appears). Only events after `t_lock` count, and answers that reach the server late are rejected. *Future work:* offset each user's stream delay so the game stays fair and resistant to cheating.

### [4] Resolver (pure code)

- Watches the event bus for the first event that qualifies after `t_lock`.
- Applies the catalog rule and emits a result record with the IDs of the events that prove it:

  ```json
  {"q":"q-17","answer":"forward","evidence":["e1042"],"facts":{"dx_m":19,"from_x":52,"to_x":71}}
  ```

- **Void:** if play stops (foul, ball out of play) or the window times out, no points are awarded or lost.
- **Corrections:** if an event is later amended, re-resolve and adjust scores.

### [5] Explainer agent (LLM + number guard)

- **Input:** the result's `facts`, the crowd split, the AI odds and the user's level. These are the only numbers it may use.
- **Output:** 1–2 sentences, adjusted to the user's level, teaching one concept per round (e.g. "switching play", "recycling possession").
- **Guard:** every number in the output must appear in `facts`. If any doesn't, use a template sentence instead.

  ```python
  nums = set(re.findall(r"\d+(?:\.\d+)?", text))
  allowed = {str(v) for v in flatten(facts).values()}
  if not nums <= allowed:
      text = template(result)
  ```

### [6] Scoring + user profile

- Updates points, streak, leaderboard, accuracy per question type and the concepts the user has seen.
- Feeds back into [2]: the question agent picks harder questions as accuracy improves and avoids concepts already taught.

---

## Hackathon architecture (keep it small)

- One **FastAPI** app with an in-process event bus.
- **WebSocket** push to the browser.
- **Microsoft Foundry** for the two LLM agents (question + explainer).
- Deploy on **Azure Container Apps (ACA)**, the option recommended in the talk when the team doesn't know Kubernetes.
- Optional, only if time allows: Cosmos DB for scores, Azure Web PubSub for scale.

---

## Worked example: one round

**Setup:** a synthetic match, Northbridge FC (NBR) vs Calder Town (CAL), 0–0. The user `alex_new` is a beginner who has played 3 rounds at 33% accuracy.

**① 33:53–34:18: a dull stretch.** NBR keeps the ball in midfield:

```
33:53 pass  Hale→Mensah   x 48→44   (back)
33:57 pass  Mensah→Duarte x 44→46   (sideways)
...  9 passes in 25s, all with x between 38 and 62
```

Detector at 34:10: `ball_mid_third=1.0, max_xT=0.04, last_shot=6m12s, poss_len=11` gives **dull_score = 0.82**. It has stayed above 0.7 for 8 seconds, so **game mode starts at 34:18.**

**② 34:18: the question agent picks.** Alex is a beginner, so it picks `next_pass_dir`:

> "Northbridge are passing it around midfield. **Next pass: which way?**
> ⬆ Forward · ↔ Sideways · ⬇ Backward"
> 💡 *Backward passes are safe; forward passes are riskier but create chances.*

**③ The probability model.** The ball is with Okafor at x=52, the nearest defender is 6m away, and this is pass 12 of the possession:

| Option | AI probability | Points if correct |
|---|---|---|
| Forward | 0.18 | 84 |
| Sideways | 0.34 | 69 |
| Backward | 0.48 | 57 |

The screen shows: *"AI thinks: Backward (48%)."* Alex goes against the AI and picks **Forward**. Answers lock at **34:24**. Final crowd split: 27% forward, 31% sideways, 42% backward.

**④ 34:26: the resolver.** The first pass after the lock:

```json
{"id":"e1042","t":"34:26","player":"Okafor","type":"pass","start":[52,30],"end":[71,22]}
```

dx = 71 − 52 = **+19m**, which is more than 5m, so the answer is **forward ✅**. Alex wins **84 points**.

**⑤ The explainer** gets `facts = {dx_m:19, ai_p_forward:18, crowd_forward:27}` and the level "beginner":

> "✅ **Forward!** Okafor spotted a gap between Calder's midfield lines and played it **19m** upfield. The AI gave that only **18%**, and just **27%** of players called it. Nice read!
> 📘 *New term:* **line-breaking pass**, a pass that skips past a line of defenders."

Guard check: the output contains 19, 18 and 27, all of which are in `facts`, so the explanation is sent.

**⑥ Scoring and profile.** Alex now has 84 points, a 1-round streak and 2 of 4 correct (50%). `line-breaking pass` is marked as learned, so next time the question agent may give Alex `keep_ball`.

**⑦ 34:31: exit.** NBR's pass reaches x=78 in the final third and xT is 0.21. **Game mode ends and the UI goes back to the live match.** No new question until 35:16 at the earliest (the 45-second cooldown).

---

## Demo video plan (under 2 minutes)

1. Replay the seeded match at 4× speed and show the dull-moment meter rising.
2. The question pops up, answers lock, the result comes in, and the explanation appears.
3. The attack starts and the game disappears, back to the live match.
4. Close on one evidence slide: how well the detector caught the planted dull stretches (hits and misses) and how well the AI's probabilities held up (calibration).

## Risks

- **Gambling perception:** use points only, never real money, and say so plainly in the pitch.
- **Overlap with Lasso** (fans challenge the AI's predictions): lead with the dull-moment timing and the casual, non-watching audience, not "predict plays".
- **What counts as "forward":** the ±5m dead zone and the `sideways` option settle edge cases. Keep the rule visible in the UI.

## Suggested build order

1. Generator [0] + resolver [4]. No LLM, quickest to make solid.
2. Dull-moment detector [1], tested against the planted dull stretches.
3. Probability model [3] + scoring [6].
4. Question [2] and explainer [5] agents on Foundry, with schema validation and the number guard.
5. UI + WebSocket push, deploy to ACA, record the demo.

## Ideas reused from the landscape report

| Idea | Source project(s) |
|---|---|
| Code computes, AI explains | Instructor's demo repo (football-results-dashboard) |
| Detecting dull vs. exciting phases of play | Control vs. Chaos, Director's Cut, Tiki-Taka |
| Withdrawing prompts that are no longer relevant | Before the Pass |
| Lock answers before the event (no hindsight) | ghost-lines |
| Play against the AI, points scaled by odds | Lasso, ParallelPitch |
| Learning feedback that changes later content | Coach in Your Ear, Matchday Explainer |
| Testing with known answers on synthetic data | Gallery |
| Re-scoring after event corrections | MatchLens |
