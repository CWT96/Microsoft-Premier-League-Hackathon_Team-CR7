# Question Bank: Design

**Date:** 2026-10-25 · **Status:** draft for review · **Stages touched:** [2] question generator, [2b] timing, [3] probability model, [4] resolver · **Tier:** P1

## Intent

The game needs many more questions than the 10 templates in `docs/pipeline.md`, so a match doesn't feel repetitive. Those questions also need rules that stop the game asking something silly, such as "Will Doyle score this match?" after Doyle already scored. The goal stays the same: the friend who doesn't watch football gets something easy and fun to do while the match is slow.

## Decisions made by the team (2026-10-25)

1. **Three kinds of questions.**
   - **Instant:** settles in seconds and needs no thinking ("next pass: forward or back?").
   - **Window:** about a period of time, such as the next 5, 10 or 20 minutes, the rest of the half, or the whole match ("goal in the next 10 minutes?").
   - **Player:** about the favourite player the user picked at the start.
2. **All three kinds appear only in dull stretches.** A result can arrive later, whenever the question settles.
3. **The kind is drawn at random** for each dull slot.
4. **Window and player questions must make sense.** Guardrails stop questions whose answer is already known, that can't fit in the time left, or about a player who isn't involved. Instant questions always make sense.
5. **Rare events** (penalties, red cards, a player scoring) are asked only when the window is long enough to make them a real toss-up.
6. **Easy and fun first.** Guardrails exist to prevent nonsense, not to make the game harder.

## Goals, non-goals, anti-goals

**Goals**
- About 28 templates in one bank file that the generator, resolver and model all read.
- Every template can be settled by code (the "code decides, LLM phrases" invariant).
- Guardrails are checked by code before a question is shown.
- New templates of an existing shape can be added by editing data, with no new code.

**Non-goals (for now)**
- Profiles of the fictional players (style, habits). That is the next piece of work.
- Questions the LLM invents. It still picks from the bank only.
- Per-user difficulty or history.

**Anti-goals (a failure even if it technically works)**
- A question whose answer the room already knows.
- A near-certain question ("Will Calder win?" at 3–0).
- A rare-event question that is almost always No.
- A question or result that pops up during an attack.
- Different shared questions for different users at the same match second.

## Constraints from the existing design

- Every question has **exactly 2 options** (the top-2 rule). If an excluded third outcome happens, the shared question is void for everyone.
- **Player templates never void.** Each user gets the same number of player questions at the same moments, tuned so P(yes) is close to 0.5 for that player.
- `contracts/` froze on 10/25 morning, so **no contract change** is allowed. The bank's "window" kind is sent as `category: "forecast"`, which already exists. Template parameters go in the free-form `question.params`.
- The event schema has no `penalty` or `substitution` type. Penalties are **derived** (see the primitives section). Substitutions are approximated (see the guardrails section).

---

## Design

### 1. Where the bank lives

**My call:** a new folder, `backend/bank/`:
- `templates.json`: the bank, one object per template.
- `bank.schema.json`: JSON Schema for the bank, validated in tests.
- `__init__.py`: `load_bank()`, `candidates(kind, state, player=None)`, and the guardrail functions.

It's a new backend folder because the generator ([2]), model ([3]) and resolver ([4]) all read it. It needs an OK from the other backend owner (tianawangty), and the PR adds the folder to `CLAUDE.md`'s map.

The bank is JSON, not YAML, so it needs no new dependency (`jsonschema` is already in `requirements.txt`).

### 2. Template fields

```json
{
  "id": "goal_window",
  "kind": "window",
  "text": "Goal in {window_label}?",
  "options": ["yes", "no"],
  "params": {"window": ["5m", "10m", "20m", "half"]},
  "counts_from": "lock",
  "resolve": {"primitive": "count_at_least", "filter": {"type": "goal"}, "n": 1},
  "prob": "poisson_count",
  "rare": false,
  "positions": null,
  "guards": ["fits_clock", "not_decided", "prob_band", "no_repeat"],
  "teaches": "goal"
}
```

| Field | Meaning |
|---|---|
| `kind` | `instant` / `window` / `player`. Mapped to contract `category`: instant → `instant`, window → `forecast`, player → `player`. |
| `text` | Plain-English template, 140 characters or fewer once filled. Placeholders: `{team}`, `{opp}`, `{home}`, `{away}`, `{player}`, `{line}`, `{window_label}`. |
| `options` | Option ids. Three or more outcomes means the top-2 rule applies. |
| `params.window` | Allowed windows: `5m`, `10m`, `20m`, `half` (until the end of this half), `match` (until full time). |
| `counts_from` | `lock` (only events after the question locks), `half_start`, or `kickoff`. Needed so "3+ goals in the match" counts goals already scored. |
| `resolve` | A primitive name plus arguments (see section 3). |
| `prob` | Which probability function in [3] prices it. |
| `rare` | `true` → the narrow toss-up band is used (see section 5). |
| `positions` | Player templates only: positions it applies to (`GK`, `DF`, `MF`, `FW`). |
| `guards` | Guardrails to run, by name. |
| `teaches` | The term the explainer can teach with this result. |

### 3. Resolver primitives

Most window and player templates use one of five generic primitives, so they need no new code. The instant ones keep their own small rules.

| Primitive | Yes when | Used by |
|---|---|---|
| `count_at_least` | events matching `filter` in the window ≥ `n` | goal, card, save, shot-on-target, fouls, player stats, penalty, red card |
| `compare_counts` | count(`filter_a`) > count(`filter_b`) in the window (a tie counts as No) | more shots |
| `first_of` | `filter_a` happens before `filter_b`; neither happening → void (shared only) | who shoots first, who scores next |
| `final_state` | a predicate on the score at window end | team wins, both teams score, equaliser |
| `next_matching` | the next event matching `filter` meets `test` (e.g. a pass is complete, longer than 25m) | instant pass questions, player next-pass |

A `filter` selects events by `type`, `outcome`, `team` (`home` / `away` / `in_possession` / `trailing`), `player` (`favourite`) and an optional `zone`.

**Penalty (derived):** a `foul` inside the fouling team's own penalty area. Coordinates are normalised so the acting team attacks left to right, so that area is x ≤ 16.5 and 13.84 ≤ y ≤ 54.16. *Assumption to check against the dataset: a foul event's `team` is the team that committed it.*

### 4. The bank (28 templates)

**Removed on 2026-10-25:** `red_card_window` (about 12% even over a whole match) and `player_scores_match` (about 33% for a striker, below the 40–60% player band). With real-world rates neither can become a toss-up.

Window labels render as "the next 5 minutes", "the next 10 minutes", "the next 20 minutes", "the rest of this half" and "this match".

**Instant (8):** windows are seconds, and no band applies unless noted.

| ID | Question | Options | Rule | Teaches |
|---|---|---|---|---|
| `next_pass_dir` *(v2)* | Next pass: forward or back? | top-2 of forward / sideways / backward | dx ±5m dead zone | line-breaking pass |
| `keep_ball` *(v2)* | Will they keep the ball for 15 seconds? | yes / no | no turnover | possession |
| `reach_final_third` *(v2)* | Will they reach the final third? | yes / no | ball x > 70 within 20s | final third |
| `shot_soon` *(v2)* | Shot in the next 60 seconds? | yes / no | any shot | shot |
| `next_pass_complete` | Will the next pass find a teammate? | yes / no | next pass `complete`; band applies | completed pass |
| `next_pass_long` | Next pass: long or short? | long / short | next pass length > 25m | long ball |
| `switch_play` | Will the ball switch to the other side of the pitch in the next 15 seconds? | yes / no | y crosses from one outer third to the other | switching play |
| `back_to_keeper` | Will the ball go back to {team}'s goalkeeper in the next 30 seconds? | yes / no | a pass received by that team's GK | playing out from the back |

**Window (13):**

| ID | Question | Windows | Rule | Extra guards | Rare |
|---|---|---|---|---|---|
| `goal_window` | Goal in {window_label}? | 5m 10m 20m half | `count_at_least` goal | | |
| `shot_on_target_window` | Shot on target in {window_label}? | 5m 10m | shots `on_target`/`saved`/`goal` | | |
| `who_shoots_first` | Who shoots first in {window_label}: {home} or {away}? | 5m 10m | `first_of`; no shot → void | | |
| `card_window` | Any card in {window_label}? | 10m 20m half | `count_at_least` card | | |
| `fouls_over_window` | {line}+ fouls in {window_label}? | 10m 20m | `line` from [3] | | |
| `more_shots_window` | Will {team} have more shots than {opp} in {window_label}? | 10m 20m | `compare_counts`; tie = No | | |
| `save_window` | Will a goalkeeper make a save in {window_label}? | 10m 20m | `count_at_least` save | | |
| `goals_over` *(v2)* | {line}+ goals in this match? | match | total goals; `counts_from: kickoff` | not_decided | |
| `team_wins` *(v2)* | Will {team} win? | match | `final_state` | | |
| `both_teams_score` | Will both teams score in this match? | match | `final_state` | not_decided | |
| `equaliser_window` | Will {team} equalise in {window_label}? | 10m 20m match | `final_state`: level at window end, or a goal by `{team}` making it level | `needs_lead_of_one` | |
| `next_goal_team` | Who scores next: {home} or {away}? | match | `first_of` goal; no goal → void | `p_none_below_0.35` | |
| `penalty_window` | Will there be a penalty in {window_label}? | half match | derived penalty | | ✓ |

The v2 templates `card_second_half` and `second_half_goal` become `card_window` and `goal_window` with window `half`, asked in the second half.

**Player (7):** each uses its `fair_line` / window tuning from [3] so P(yes) ≈ 0.5.

| ID | Question | Positions | Rule | Rare |
|---|---|---|---|---|
| `player_touch_window` *(v2)* | Will {player} touch the ball in {window_label}? | all | any event by the player; window tuned | |
| `player_stat_over` *(v2)* | Will {player} make {line}+ {stat} this half? | all (stat by position) | GK saves, DF clearances, MF completed passes, FW shots | |
| `player_next_pass_forward` | Will {player}'s next pass go forward? | DF MF | next pass by the player within 10m ends further up the pitch (`end.x > start.x`); no pass → No. Replaced "find a teammate?" (75–85% yes, never near 50/50) | |
| `player_next_pass_long` | {player}'s next pass: long or short? | GK DF MF | > 25m; no pass in 10m → short | |
| `player_shot_window` | Will {player} take a shot in {window_label}? | MF FW | window tuned | |
| `player_win_ball_window` | Will {player} win the ball back in {window_label}? | DF MF | tackle `won` or interception; window tuned | |
| `player_save_window` | Will {player} make a save in {window_label}? | GK | window tuned | |

### 5. Guardrails (code, run before a template becomes a candidate)

| Guard | Applies to | Rule |
|---|---|---|
| `fits_clock` | window, player | The window must end before the half ends (45:00 / 90:00 nominal). `half` is allowed until 10 minutes before the half ends; `match` until 75:00. |
| `not_decided` | templates with `counts_from` ≠ `lock` | Run the resolver on the events so far. If the answer is already Yes, or can no longer change, skip it. |
| `prob_band` | all except the v2 instant templates | P(yes), or P of the leading option, from [3] must lie in the band: **normal 0.15–0.85**, **rare 0.20–0.80** (loosened from 0.35–0.65 on 2026-10-25, because real-world rates never reach it), **player 0.40–0.60** (v2 rule). For a rare template, try windows from shortest to longest and use the first that lands in the band. If none does, skip it. |
| `no_repeat` | all | The template isn't currently open and wasn't the last one asked. |
| `max_open_windows` | window | At most 2 window questions waiting to settle at once. |
| `player_active` | player | The favourite player has an event in the last 10 minutes and hasn't been sent off. *This approximates substitutions, which the event schema doesn't have.* |
| `position_fit` | player | The player's position is in the template's `positions`. |
| `needs_lead_of_one` | `equaliser_window` | One team leads by exactly one goal; `{team}` is the trailing team. |
| `p_none_below_0.35` | `next_goal_team` | The chance of no more goals must be below 0.35, so the question rarely voids. |

Expected effect: with the 20–80% rare band, `penalty_window` passes only as a "this match" question asked before about 23:00 (P ≈ 23% at 10:00). Later it is skipped. That's acceptable, and the demo must not depend on it.

### 6. How a question is picked (per dull slot)

```
Timing [2b] opens a slot (existing rules: cooldown 45s, cap 8 per half, never during an attack)
  │
  ├─ 1. Draw a kind at random: instant / window / player, 1/3 each.
  │     Seeded RNG: seed = hash(match_id, open_at), where open_at is the slot's match second.
  │     The detector is deterministic, so every room gets the same slots at the same seconds;
  │     a host's skip just jumps past some of them.
  │
  ├─ 2. Build candidates for that kind: expand each template's params (windows, lines, team),
  │     run its guards, and attach P from [3]. Keep what passes.
  │
  ├─ 3. If there are no candidates → fall back to instant. If instant is also empty → skip the slot.
  │
  ├─ 4a. Shared kinds (instant, window): the question generator agent picks ONE candidate and
  │      writes the text. Its prompt asks for variety (not recently asked), a fit to the moment
  │      (e.g. an equaliser question when a team trails) and P near 0.5. Output is validated.
  │      On failure or a cache miss → seeded random choice + template text.
  │
  └─ 4b. Player kind: each user gets a candidate about their own player, chosen by seeded random,
         with template text (no LLM call).
```

**My calls:**
- **The LLM picks within the drawn kind.** The kind is random, as decided. Letting the agent choose the template inside it keeps "the agent selects the best question" (the original ask, and the Agent design score) while staying within code-checked candidates.
- **Player questions use template text, with no LLM.** The random draw makes the combinations too many to pre-warm the cache for 22 players.
- **Player fallback:** if the user's player fails every player template (for example, sent off), they get `player_touch_window` about another player in the same position on their chosen team. The question still counts as a player question.
- **Equal player counts:** a player slot is issued only if **every user in the room** (bot friends included) has a valid candidate after that fallback. Otherwise the slot falls back to an instant question for everyone. This keeps the v2 rule that everyone gets the same number of player questions at the same moments.

**Answer window and replay speed (all kinds):** every question locks **6 real seconds** after it appears, and the replay runs at 1× only during those 6 seconds. Instant questions stay at 1× until their result is shown, which is a few seconds (v2 rule). Window and player questions go back to the chosen speed at lock and settle later in the background. `counts_from: lock` therefore means events after that 6-second lock. The question card is unchanged. The frontend only needs to show results that arrive later, which the `question.result` message already supports.

**Speed:** building candidates means about 100 small formula evaluations per slot. That's well under a millisecond, and slots are at least 45 seconds apart. The only slow step is the LLM pick (1–2 seconds live; near zero in `LLM_MODE=cache`). In live mode it has a **2-second timeout**, after which the seeded random choice is used, so a question is never late.

**Results that settle during an attack** are held and shown when the attack ends, as in v2. Several pending results are shown one after another.

### 7. Probability model inputs ([3])

**My call:** window templates are priced with a **Poisson model using fixed, hard-coded rates per 90 minutes**. Starting values: goals 2.7, shots 25, shots on target 9, cards 4, red cards 0.15, penalties 0.3, fouls 22, saves 6. These are scaled to the window length and optionally nudged by the rate so far in this match.

The rates are **not** taken from the dataset, because the dataset is the same match being replayed. Rates computed from it would leak the future into the questions.

**Player fair lines** use the same approach: hard-coded rates per 90 minutes for each position, kept in `backend/model/priors.py`. Starting values:

| Position | Touches | Saves | Clearances | Completed passes | Shots | Tackles + interceptions | Goals |
|---|---|---|---|---|---|---|---|
| GK | 35 | 3 | — | 25 | — | — | — |
| DF | 60 | — | 4 | 40 | 0.5 | 3 | 0.05 |
| MF | 70 | — | 1 | 45 | 1.2 | 2.5 | 0.15 |
| FW | 40 | — | 0.3 | 20 | 2.5 | 0.8 | 0.4 |

**Instant questions** keep v2's frequency tables built from the dataset. They aggregate over thousands of situations rather than looking ahead in time, so the leak is small and accepted for the hackathon.

### 8. Changes to other parts of the design

- `docs/pipeline.md`:
  - **§[2b]:** remove the kickoff/half-time stacks and the fixed player slots at 40:00 and 70:00. Every question now comes through dull slots.
  - **§[2]:** the catalog table points to `backend/bank/templates.json` as the source of truth.
- `CLAUDE.md`: add `backend/bank/` to the folder map.
- **Scoring:** the category budgets (400 / 300 / 300) and the formula are unchanged. With random draws the number of questions per category varies by match, which `per_q_c = B_c / n_c` already handles. If a category gets no questions, its budget drops out of `max_possible`.
- **Frontend:** no change to the question card. The kickoff/half-time stack UI is no longer needed (tell CWT96).

### 9. Testing

- The bank validates against `bank.schema.json`.
- Every template has a resolver primitive and a `prob` function, and its filled text is 140 characters or fewer with the longest roster names.
- One test per guard with a hand-made state, for example:
  - `goals_over` with line 2 is skipped once 2 goals have been scored;
  - `goal_window 20m` is skipped at 80:00;
  - `penalty_window` with window `5m` fails the rare band.
- Selection is deterministic: the same `match_id` and slot always give the same kind and the same fallback choice.

## Left to the builder

- Exact sub-score values and the zone used for `switch_play`.
- How the trailing team or `in_possession` team is passed into `{team}`.
- The rate nudge in section 7, if any.
- Wording of each template, keeping plain English for non-fans.
- Order of implementation, as long as v2's 10 templates keep working at every step.
