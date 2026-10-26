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
| `model/` | [3] Probability model | `option_probs(template_id, state, params) -> dict[str, float]`; `fair_line(template_id, player_id, state) -> params`. Player templates read `params["player_id"]` (copy it from `Question.player_id`; the resolver reads that field). Warm `get_model()` at app startup. |
| `agents/question_generator.py` | [2] Question generator agent (Stephen) | `propose(state: DetectorState, moment: Moment, user_player_id: str \| None) -> Question \| None` (template + params + text, top-2 options). Until it exists the app uses `agents/question_generator_stub.py`. |
| `agents/timing.py` | [2b] Timing agent: when and why, never what | `TimingAgent.step(state: DetectorState) -> TimingOutput` (`.moments`, `.decisions`) |
| `app/pacer.py` | Per-user pace through the shared moments | `Pacer.next_card(t, open_moments) -> Moment \| None` |
| `resolve/` | [4] Resolver | `resolve(question, events_after_lock) -> QuestionResult \| None` |
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
