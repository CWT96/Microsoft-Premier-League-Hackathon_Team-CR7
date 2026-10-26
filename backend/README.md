# backend/

**Owners:** `stephenwzkong`, `tianawangty`. Python 3.11 + FastAPI, one process, in-process event bus.

Suggested split (swap freely, but write down who owns what in the PR description):

| Lane | Folders | Nature |
|---|---|---|
| **Core** (deterministic, fully unit-tested) | `ingest/`, `detect/`, `model/`, `resolve/`, `scoring/` | Pure Python. No network, no LLM. |
| **Agents + wiring** | `agents/`, `llm_cache/`, `social/`, `app/` | LLM calls, cache, WebSocket, REST, replay control. |

## Module map

| Folder | Pipeline stage | Main entry point (target signature) |
|---|---|---|
| `ingest/` | [0] Match data adapter: reads the Hudl/StatsBomb dataset in `../data/source/` (`ingest/statsbomb.py`) | `load_match(match_id: str) -> Match` (`.events`, `.roster`, `.tracking` or None) |
| `detect/` | [1] Dull-moment + attack-end detector | `Detector.step(half: int, t: float, new_events: list[MatchEvent], tracking_frame: TrackingFrame \| None) -> DetectorState` |
| `model/` | [3] Probability model (Stephen): Poisson pricing + position priors used by the bank | `poisson.PRICERS[...]`, `tune_player(...)`, `count_already(...)` |
| `bank/` | Question bank (Stephen): 28 templates, guards, slot and stack selection, settle | `select_for_slot(state, open_at, users, bank, exclude=...)`, `select_stack(...)`, `settle_candidate(...)` |
| `agents/question_generator.py` | [2] Adapter: timing moments → bank questions, one decision per moment for every roster player | `BankGenerator(match).propose(state, moment, user_player_id) -> Question \| None`; `.probs(qid)` |
| `agents/quiet.py` | Templates the match settles before a person can answer in open play (measured on `data/source/`) | `unsafe_in_open_play() -> frozenset[str]` |
| `agents/timing.py` | [2b] Timing agent: when and why, never what | `TimingAgent.step(state: DetectorState) -> TimingOutput` (`.moments`, `.decisions`) |
| `app/pacer.py` | Per-user pace through the shared moments | `Pacer.next_card(t, open_moments) -> Moment \| None` |
| `resolve/` | [4] Resolver: bank primitives (`primitives.py`, Stephen) + `watcher.Resolver` (counts from the card opening) | `Resolver(generator).watch(question, ...)`, `.step(half, t, new_events) -> list[Outcome]` |
| `agents/explainer.py` | [5] Explainer agent + number guard | `explain(result, facts) -> str` |
| `scoring/` | [6] Scoring formula | `final_score(user_answers, issued) -> FinalScore` |
| `agents/scoring_summary.py` | [6b] Scoring agent (LLM summary only) | `summarize(final_score) -> str` |
| `social/` | Rooms + **simulated users**: global crowd answers, bot friends, ops funnel numbers (match data comes from `data/source/`, user behaviour is generated here) | `crowd_split(qid, scope) -> dict[str, float]` |
| `app/` | FastAPI app, `/ws`, `/api/*`, replay clock | `uvicorn backend.app.main:app` |

Payload shapes come from `../contracts/`. Mirror them as Pydantic models in `app/models.py` and do not invent fields. Internal types shared between stages (`Match`, `DetectorState`, `Moment`, `TimingDecision`, `TimingOutput`, `Outcome`, `Answer`) live in `core/types.py`.

## Rules
- **Code decides, LLM phrases.** Resolver and scoring never call an LLM.
- Every LLM call goes through `agents/llm.py`. In `LLM_MODE=cache` it reads `llm_cache/`; in `live` it calls Foundry or GitHub Models and writes the cache. Cache key = hash(agent name, prompt, match_id, qid).
- LLM output is JSON validated against a schema. On failure, fall back to a template.
- Tests live in `tests/`, one file per module (`tests/test_resolve.py` …). No network in tests.

## Run (once code exists)
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements.txt
uvicorn backend.app.main:app --reload
pytest backend/tests
```
