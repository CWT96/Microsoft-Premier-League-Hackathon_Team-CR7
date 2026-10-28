# CLAUDE.md

This file is guidance for Claude Code and for the three of us working in this repo. **README.md** says what the product is. **`docs/pipeline.md`** is the full design. This file covers where things live, how the parts connect, and how we use git.

## Project in one line
A phone second screen for non-fans watching football with friends: two-option questions in dull stretches, catch-up cards after attacks, a 0–1000 score card and a mock membership sign-up at full time. Synthetic data only. Deadline 2026-10-27 23:59 PT.

## Folder architecture and ownership

```
README.md            product overview, judging map               all
CLAUDE.md            this file                                   all
docs/pipeline.md     full design (source of truth for behaviour) all
docs/source_reports/ background research (read-only)              all
contracts/           backend ↔ frontend payloads (JSON Schema)   all; both sides approve changes
backend/             Python 3.11 + FastAPI                       stephenwzkong, tianawangty
  app/               FastAPI app, /ws, /api/*, replay clock, Pydantic models mirroring contracts/
  ingest/            [0] adapter: teammate's match dataset (data/source/) → MatchEvent stream
  detect/            [1] dull-moment + attack-end detector
  bank/              question bank: templates, guards, slot selection
  model/             [3] probability model (top-2, hint, fair lines)
  resolve/           [4] resolver: bank primitives + watcher (counts from the card opening)
  scoring/           [6] scoring formula (pure functions)
  agents/            [2] question generator, [2b] timing, [5] explainer, [6b] scoring summary, llm.py client
    prompts/         prompt files, one per agent
  llm_cache/         committed LLM replay cache for the dataset's match(es)
  social/            rooms + simulated users: global crowd, bot friends, ops funnel
  tests/             pytest, one file per module
frontend/            phone UI, framework chosen by CWT96         CWT96
data/source/         simulated MATCH dataset (Hudl/StatsBomb format), generated  backend
data/generator/      seeded match simulator + validator that builds data/source/ backend
data/generated/      derived files, gitignored                    backend
infra/               .env.example, Dockerfile, ACA notes          backend
.github/CODEOWNERS   auto-assigns reviewers per folder
```

Each folder has a `README.md` stating its owner and what belongs there. Read it before adding files. **Stay inside your own folder.** If you need to change someone else's folder, ask in the team chat first or open a PR and request their review.

**Two kinds of simulated data:** *match data* (events, rosters) comes only from `data/source/`, which a teammate uploads (format and rules in `data/README.md`). *User data* (crowd answers, bot friends, funnel) is generated in `backend/social/`. Never mix the two.

Suggested backend split (the two owners may swap): **core** = `ingest/ detect/ model/ resolve/ scoring/` (deterministic, no network); **agents + wiring** = `agents/ llm_cache/ social/ app/ infra/`.

## Interfaces

### Backend ↔ frontend: `contracts/`
- `contracts/README.md` lists every WebSocket message and REST endpoint. `*.schema.json` define the payloads.
- The WebSocket envelope is `{"type": "...", "data": {...}}` on `/ws?match_id=&room_id=`.
- **Freeze: 10/25 morning.** After that, changes are additive only (new optional fields) and need a PR approved by one backend owner and CWT96.
- Examples in `contracts/README.md` must validate. Run the snippet at the bottom of that file after any edit.

### Inside the backend (target signatures, see `backend/README.md`)
`load_match(match_id)` → `Detector.step(half, t, new_events, tracking_frame)` → `TimingAgent.step(state)` → per-user pacer → `BankGenerator.propose(state, moment, user_player_id)` (bank `select_for_slot` / `select_stack`; open play excludes `quiet.unsafe_in_open_play()`) → `Resolver.step(...)` (bank `settle_candidate`) → `explainer.explain(result, facts)` → `scoring.final_score(...)` → `scoring_summary.summarize(...)`.

### Invariants (do not break)
- **Code decides, LLM phrases.** The resolver and scoring never call an LLM. Agents never change a result or a number.
- Every question has exactly 2 options. If the excluded third outcome happens, the question is void for everyone. The UI never shows the word "void".
- Questions come from one shared stream of timing moments: every user who picks up a moment gets the same question (except favourite-player moments, which name each user's own player), and every moment locks before its outcome can happen. Each user moves through the stream at their own pace. Favourite-player moments open at the same time for everyone, and their difficulty is tuned to P≈0.5.
- Timing decides when and why (the moment's situation). The question generator alone decides what is asked.
- Room flow (`/ws`): no point numbers or running total on screen during the match. Only ✓ and coins. Demo mode (`/demo/ws`, the judged fan app) is the agreed exception: it shows live points and a running total.
- Every number in LLM text must appear in that call's `facts` (number guard), or a template is used instead.
- Synthetic data only. Fictional clubs, crests and player names everywhere, the demo included: it shows the synthetic match as Northbridge vs Calder with the dataset's own fictional names (`backend/demo/names.py`). No real club or player names or crests.
- When a match dataset appears in `data/source/`, first run the three must-have checks at the top of `data/README.md` (ball coordinates, player positions with fitting stats, both dull spells and attacks). Report any gaps before writing ingest code.

## Infrastructure
- **Runtime:** one FastAPI process, in-process event bus, in-memory state. A restart restarts the match.
- **LLM:** Microsoft Foundry. All calls go through `backend/agents/llm.py`.
  - `LLM_MODE=cache` (default, deployed) replays `backend/llm_cache/` at zero cost.
  - `LLM_MODE=live` calls Foundry (Azure free credit) or GitHub Models (free tier) and writes the cache.
  - Cache key: `hash(agent, prompt, match_id, qid)`.
- **Deploy:** Azure Container Apps (Consumption, free grant), one container serving API + WebSocket + built frontend. No paid services.
- **Env vars:** see `infra/.env.example`. Copy it to `.env` at the repo root. **Never commit `.env` or keys.**

## Commands
```bash
# backend
python -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements.txt
uvicorn backend.app.main:app --reload       # http://localhost:8000, single worker only
pytest                                      # fast suite; must pass before every PR
pytest -m slow                              # full-match WebSocket end-to-end run (~3 min)
python -m backend.agents.timing_sim          # timing simulator → data/generated/timing_report.md
python -m data.generator.generate           # rebuild data/source/ (deterministic)
python -m data.generator.check              # validate the dataset (must-haves + realism)
# frontend: see frontend/README.md once CWT96 picks the stack
```

## Git workflow (everyone follows the same rules)

**Branches**
- `main` must always run and be demo-able. **No direct pushes to `main`.**
- Work on short-lived branches named `<area>/<topic>`, where area is `backend`, `frontend`, `contracts`, `docs`, `infra` or `data`. Examples: `backend/resolver`, `frontend/question-card`, `contracts/catchup-card`.
- One branch = one focused change. Merge it within a day. Do not keep long-lived personal branches.

**Daily sync**
```bash
git checkout main && git pull origin main          # start of every work session
git checkout -b backend/resolver                   # new branch from fresh main
# ... work, commit ...
git fetch origin && git rebase origin/main         # before opening or updating a PR
git push -u origin backend/resolver                # then open a PR on GitHub
```
After a PR merges, delete the branch and start the next one from a fresh `main`.

**Commits:** `[area] Verb thing: description`, where the verb is one of Add, Fix, Refactor, Config, Docs. Example: `[backend] Add resolver: next_pass_dir and keep_ball rules`.

**Pull requests**
- CODEOWNERS requests reviewers automatically. At least **1 approval** is required.
- `contracts/` PRs need approval from both sides.
- Keep PRs small, ideally under about 300 changed lines, so review takes minutes.
- Checklist for the PR description:
  - [ ] What changed and which pipeline stage(s) (`[0]`–`[6b]`)
  - [ ] Tier (P0/P1/P2)
  - [ ] `pytest backend/tests` passes (backend) / app runs (frontend)
  - [ ] Contract examples still validate (if `contracts/` touched)
  - [ ] No secrets, no real match data, no real people's data
- Resolve merge conflicts on your own branch by rebasing. Never force-push `main`.

**What is committed and what is not**
- Committed: code, `contracts/`, `docs/`, `data/source/` (files under 50 MB), `backend/llm_cache/`, `infra/.env.example`.
- Ignored: `.env`, `.venv/`, `node_modules/`, `dist/`, `data/generated/`, personal logs (`quality_reports/`, `SESSION_REPORT.md`).

## Priorities
Work strictly in tier order. P0 must work end to end before anyone starts P1. The table with cut-offs is in `README.md` and `docs/pipeline.md` § Priorities and cut lines. Anything unfinished at its cut-off becomes a static mock page.

## Conventions
- **Python:** type hints on public functions, short docstrings, `pathlib`, no hardcoded absolute paths, one pytest file per module, no network in tests.
- **Prose (docs, UI copy, LLM prompts):** English, plain words. The audience is someone who does not know football terms. Define a term the first time it appears.
