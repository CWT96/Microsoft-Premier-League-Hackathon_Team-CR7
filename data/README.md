# data/

**Owners:** backend (review), plus the teammate who provides the match dataset.

> **⚠ For AI assistants and teammates: read this before touching `data/source/`.**
> Most of the game depends on what is *inside* the match dataset, not only on where it is stored. When a dataset appears in `data/source/`, **first check it against the three must-haves below and report anything missing to the team, before writing any ingest code.**
>
> 1. **Ball start and end coordinates for every on-ball event.** Without them we lose dull-stretch detection, `next_pass_dir`, `reach_final_third` and the 2D pitch.
> 2. **Player positions (GK / DF / MF / FW) and stats that fit each position** (goalkeepers save, defenders clear, forwards shoot). Without them, favourite-player questions cannot be tuned to roughly 50/50 and the fairness rule breaks (`docs/pipeline.md` §[3]).
> 3. **Both long midfield passing spells and sudden attacks.** Without dull stretches, no questions pop up. Without attacks, there is no catch-up card and nothing to show in the demo.
>
> Send this checklist to the teammate **before** they build the dataset. With 3 days to the deadline there is no time to redo the data. The full field list is in "What the pipeline needs" below.

There are two kinds of simulated data in this project, and they live in different places:

| What | Who makes it | Where |
|---|---|---|
| **Match data**: the events of one or more simulated matches (passes, shots, saves…) and the rosters | A teammate uploads it | **`data/source/`** (this folder) |
| **User data**: global crowd answers, bot friends, sign-up/redemption funnel | We generate it in code | `backend/social/` (crowd, bots) and the ops funnel endpoint. Never stored here. |

## `data/source/`: upload the match dataset here

**Status: empty. Waiting for the upload.** Until it arrives, team names (`NBR`, `CAL`), player names and the `match_id` in the docs are placeholders.

Upload rules:
- Put the files directly in `data/source/`, any format (CSV, JSON or Parquet), via a PR on a branch named `data/match-source`. The files are committed so judges get the same data.
- Keep each file **under 50 MB**. GitHub rejects anything over 100 MB. For bigger data, commit a smaller sample and put the full file somewhere else, linked here.
- Add a short note at the bottom of this README with the file names, the columns or fields, the units, and how many matches the dataset has.
- Do not hand-edit after upload. Corrections go in a new PR so the replay and the LLM cache can be rebuilt.

What the pipeline needs from the dataset (`backend/ingest/` maps it to `contracts/event.schema.json`, so names and format can differ):

| Needed | Used by |
|---|---|
| Event time (match clock), half, team, acting player, event type | everything |
| Ball start/end position (pitch coordinates) | dull-moment detector, `next_pass_dir`, `reach_final_third`, 2D pitch |
| Outcomes: pass complete/incomplete, shot on/off target, save, goal, foul, card, ball out | resolver |
| Roster with player positions (GK / DF / MF / FW) | entry screen, favourite-player questions |
| Position-realistic stats (GK saves, DF clearances, FW shots) | fair lines for favourite-player questions |
| Some long, slow midfield spells **and** some sudden attacks | dull-moment detector, demo |

Anything missing gets flagged in the ingest PR. The adapter may fill small gaps (e.g. derive `half` from time), but never invents match events.

## `data/generated/`
Gitignored. Derived files built from `data/source/` (normalised event stream, probability tables). Rebuild them with the code; never edit them by hand.

---

### Dataset note (to be filled in by the uploader)
- Files:
- Fields and units:
- Matches:
