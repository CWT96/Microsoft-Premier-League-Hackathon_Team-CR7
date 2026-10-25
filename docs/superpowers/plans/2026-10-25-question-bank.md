# Question Bank Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A 28-template question bank with code-checked guardrails, Poisson probabilities and a seeded per-slot picker, so every dull slot gets a sensible instant, window or player question.

**Architecture:**
- `backend/bank/` holds the bank data, the loader, the guards, candidate building and slot selection.
- Settling rules go in `backend/resolve/primitives.py` and Poisson pricing in `backend/model/poisson.py` and `backend/model/priors.py`, the stages that own them.
- Everything is pure Python with no network. The LLM picker is an injected callable, so tests never call an LLM.

**Tech Stack:** Python 3.11, standard library (`json`, `math`, `random`, `hashlib`, `dataclasses`, `concurrent.futures`), `jsonschema`, `pytest`.

**Spec:** `docs/superpowers/specs/2026-10-25-question-bank-design.md`

## Global Constraints

- Code decides, LLM phrases. Nothing in `resolve/`, `model/` or `bank/` calls an LLM. The picker is injected.
- Every question has exactly 2 options. Templates with 3 outcomes show the top 2 by probability.
- Player templates never void. Shared templates void when an excluded outcome happens, when `first_of` sees neither side, or when an instant window times out.
- Contract mapping: kind `instant` → category `instant`, `window` → `forecast`, `player` → `player`. **No changes to `contracts/`.**
- Bands: normal 0.15–0.85, rare 0.20–0.80, player 0.40–0.60. The v2 instant templates (`next_pass_dir`, `keep_ball`, `reach_final_third`, `shot_soon`) have no band.
- Nominal half ends: half 1 = 2700 s, half 2 = 5400 s. `half` window allowed while `t ≤ half_end − 600`; `match` window allowed while `t ≤ 4500`.
- Every question locks at `open_at + 6`. `counts_from: lock` means events with `t > lock_at`.
- Seed = first 16 hex digits of `sha256(f"{match_id}:{int(open_at)}")`, read as an integer.
- LLM picker timeout: 2.0 s, after which the seeded random choice is used.
- Filled question text is 140 characters or fewer. Option labels are 24 characters or fewer.
- Python: type hints on public functions, short docstrings, `pathlib`, one pytest file per module, no network in tests.
- Commits: `[backend] Add thing: description`. Work on branch `backend/question-bank`, cut from fresh `main`.

## Review Focus

1. **A window that would run past half time** (e.g. `10m` at 40:00) must be rejected, not resolved across the break. Test in Task 4.
2. **A goal in stoppage time** (t > 5400) must still count for `match` windows, which close on the `full_end` event, not at 5400. Test in Task 2.
3. **Events missing `start` coordinates** (fouls in some datasets) must not crash penalty detection. They count as "not a penalty". Test in Task 2.
4. **A sent-off or inactive favourite player in a room** must not leave that user without a player question while others get one. Either a fallback player or the whole slot goes instant. Test in Task 6.
5. **An LLM picker that returns an unknown key, raises or hangs** must fall back to the seeded choice within 2 s. Test in Task 6.

---

## File structure

| File | Responsibility |
|---|---|
| `backend/bank/types.py` | Dataclasses: `Template`, `Player`, `MatchState`, `Window`, `Candidate`, `SlotDecision` |
| `backend/bank/templates.json` | The 28 templates (data) |
| `backend/bank/bank.schema.json` | JSON Schema for one template |
| `backend/bank/loader.py` | `load_bank()`, `fill_text()`, `window_label()` |
| `backend/resolve/primitives.py` | `event_matches()`, `is_penalty()`, `settle()` |
| `backend/model/priors.py` | Rate tables (match-level and per position) |
| `backend/model/poisson.py` | Probability functions + `price()` dispatcher + `tune_player()` |
| `backend/bank/guards.py` | One function per guard, plus `GUARDS` registry |
| `backend/bank/candidates.py` | `build_candidates()` |
| `backend/bank/select.py` | `select_for_slot()` |
| `backend/tests/test_bank_loader.py`, `test_primitives.py`, `test_poisson.py`, `test_guards.py`, `test_candidates.py`, `test_select.py` | Tests |

**Ownership note:** `resolve/` and `model/` sit in the "core" lane. Before starting Tasks 2–3, tell tianawangty which new files you are adding, so the P0 work on `next_pass_dir` and the frequency tables doesn't collide. This plan does **not** touch the v2 instant resolver or frequency tables; it provides `INSTANT_DEFAULTS` until those land.

---

### Task 1: Types, bank file, schema and loader

**Files:**
- Create: `backend/bank/__init__.py`, `backend/bank/types.py`, `backend/bank/templates.json`, `backend/bank/bank.schema.json`, `backend/bank/loader.py`
- Modify: `CLAUDE.md` (folder map: add `bank/  question bank: templates, guards, slot selection` under `backend/`)
- Test: `backend/tests/test_bank_loader.py`

**Interfaces:**
- Produces, in `types.py`:
  ```python
  Kind = Literal["instant", "window", "player"]
  CATEGORY = {"instant": "instant", "window": "forecast", "player": "player"}

  @dataclass(frozen=True)
  class Template:
      id: str; kind: Kind; text: str; options: list[str]
      params: dict[str, list]        # e.g. {"window": ["5m","10m"]}, {"line": "tuned"}, {"window_s": "tuned"}
      counts_from: Literal["lock", "half_start", "kickoff"]
      resolve: dict                  # {"primitive": str, ...args}
      prob: str                      # key in poisson.PRICERS
      rare: bool; positions: list[str] | None; guards: list[str]; teaches: str
      team_from: Literal["none", "possession", "trailing", "each"] = "none"
      v2_no_band: bool = False

  @dataclass(frozen=True)
  class Player: player_id: str; name: str; team: str; position: Literal["GK","DF","MF","FW"]

  @dataclass(frozen=True)
  class Window:
      start_t: float                 # events with t > start_t count
      end_t: float | None            # None = closes on half_end (half) or full_end (match) event
      end_marker: Literal["half_end", "full_end"] | None

  @dataclass
  class MatchState:
      match_id: str; t: float; half: int; home: str; away: str
      team_names: dict[str, str]; score: dict[str, int]      # keyed by team code
      possession_team: str | None; events: list[dict]        # all events so far, contract MatchEvent shape
      open_template_ids: list[str]; last_template_id: str | None; open_window_count: int
      roster: dict[str, Player]

  @dataclass(frozen=True)
  class Candidate:
      template_id: str; kind: Kind; category: str; params: dict
      text: str; options: list[str]; p: dict[str, float]
      window: Window; resolve_by: float | Literal["HT", "FT"]; player_id: str | None
      @property
      def key(self) -> str: ...      # f"{template_id}|{json.dumps(params, sort_keys=True)}|{player_id}"

  @dataclass(frozen=True)
  class SlotDecision:
      open_at: float; kind: Kind
      shared: Candidate | None                  # set for instant/window
      per_user: dict[str, Candidate] | None     # user_id → candidate, set for player
  ```
- Produces, in `loader.py`:
  - `load_bank(path: Path | None = None) -> dict[str, Template]`, which validates every entry against `bank.schema.json` and raises `ValueError` on a duplicate id.
  - `window_label(window: str | int) -> str`
  - `fill_text(template: Template, params: dict, state: MatchState, player: Player | None) -> str`

`templates.json` is a JSON array of all 28 rows from spec §4, using the field rules of spec §2. Values the spec leaves open, decided here:
- `team_from`: `possession` for `more_shots_window`, `back_to_keeper` and `keep_ball`; `trailing` for `equaliser_window`; `each` for `team_wins`.
- Tuned params are written as the string `"tuned"`: `{"line": "tuned"}` for `fouls_over_window`, `goals_over` and `player_stat_over`; `{"window_s": "tuned"}` for the player window templates.
- `v2_no_band: true` on the four v2 instant templates.
- `rare: true` only on `penalty_window`.

- [ ] **Step 1: Write the failing tests**

```python
def test_bank_has_28_templates_by_kind():
    bank = load_bank()
    kinds = Counter(t.kind for t in bank.values())
    assert len(bank) == 28 and kinds == {"instant": 8, "window": 13, "player": 7}

def test_every_template_has_resolver_and_pricer():
    from backend.resolve.primitives import PRIMITIVES
    from backend.model.poisson import PRICERS
    for t in load_bank().values():
        assert t.resolve["primitive"] in PRIMITIVES and t.prob in PRICERS

def test_duplicate_id_raises(tmp_path): ...            # two entries with id "goal_window" → ValueError

def test_window_labels():
    assert window_label("5m") == "the next 5 minutes"
    assert window_label("half") == "the rest of this half"
    assert window_label("match") == "this match"
    assert window_label(60) == "the next minute"
    assert window_label(120) == "the next 2 minutes"
    assert window_label(90) == "the next 90 seconds"

def test_filled_text_fits_140_with_long_names(): ...   # 30-char team and player names, longest params → all ≤ 140
```

`test_every_template_has_resolver_and_pricer` is marked `@pytest.mark.skip` until Task 3 lands. Remove the skip in Task 3, Step 4.

- [ ] **Step 2: Run** `pytest backend/tests/test_bank_loader.py -v`. Expected: FAIL (ImportError).
- [ ] **Step 3: Implement** the types, the schema (all fields required except `team_from` and `v2_no_band`; `kind` enum; `options` with 2–3 items), `templates.json` and the loader.
- [ ] **Step 4: Run** `pytest backend/tests/test_bank_loader.py -v`. Expected: PASS (1 skipped).
- [ ] **Step 5: Commit** `[backend] Add question bank: 28 templates, schema, loader`

---

### Task 2: Resolver primitives

**Files:**
- Create: `backend/resolve/primitives.py`
- Test: `backend/tests/test_primitives.py`

**Interfaces:**
- Consumes: `Window` from Task 1.
- Produces:
  - `@dataclass(frozen=True) class Ctx: home: str; away: str; possession_team: str | None; trailing_team: str | None; favourite: str | None; team: str | None; gk_ids: dict[str, str]`
  - `event_matches(event: dict, flt: dict, ctx: Ctx) -> bool`. Filter keys:
    - `type` (str or list)
    - `outcome` (str or list)
    - `team`: one of `home`, `away`, `possession`, `trailing`, `param`, `any`
    - `player`: `favourite`
    - `derived`: `penalty`
  - `is_penalty(event: dict) -> bool`: True when type is `foul`, `start` exists, `start[0] <= 16.5` and `13.84 <= start[1] <= 54.16`.
  - `settle(spec: dict, events: list[dict], window: Window, ctx: Ctx, closed: bool) -> str | None` returns an option id, `"void"`, or `None` (still open). It considers only events with `window.start_t < t` (and `t <= end_t` when set). A window is closed when `closed=True`, when `t > end_t`, or when a matching `end_marker` event appears in `events`.
  - `PRIMITIVES = {"count_at_least", "compare_counts", "first_of", "final_state", "next_matching"}`

Settling rules (spec §3), decided here:
- `count_at_least {filter, n}`: "yes" as soon as the count reaches `n`; "no" when closed.
- `compare_counts {filter_a, filter_b}`: when closed, "yes" if a > b, otherwise "no".
- `first_of {filter_a, filter_b, options: [a_id, b_id]}`: the first matching event decides; closed with neither → "void".
- `final_state {predicate}` with predicate `team_wins | both_score | equalise`. Score is counted from **all** goal events, not only those inside the window.
  - `equalise`: "yes" the moment the score is level; "no" when closed.
  - `both_score`: "yes" once both teams have scored; otherwise "no" when closed.
- `next_matching {filter, test}`, where `test` is one of:
  - `{"complete": true}`
  - `{"min_length_m": 25}` (option `long`, else `short`)
  - `{"dx_dead_zone_m": 5}` (forward / sideways / backward)

  It returns the option for the first matching event. If none appears before close, it returns the template's `no_event` value from `spec` (e.g. `"no"`, `"short"` or `"void"`).

- [ ] **Step 1: Write the failing tests**

```python
def test_goal_in_window_yes_early(): ...               # goal at t=start+100, end_t=start+600, closed=False → "yes"
def test_goal_in_window_no_when_closed(): ...          # no goal, closed=True → "no"
def test_pending_returns_none(): ...                   # no goal, closed=False → None
def test_stoppage_goal_counts_for_match_window():      # Review Focus 2
    events = [goal(t=5500), {"type": "full_end", "t": 5580, ...}]
    assert settle(goals_over_2, events, Window(0, None, "full_end"), ctx, closed=False) == "yes"   # with 1 earlier goal
def test_compare_counts_tie_is_no(): ...               # 2 shots each → "no"
def test_first_of_neither_is_void(): ...               # no shots, closed → "void"
def test_penalty_detection():
    assert is_penalty({"type": "foul", "start": [10, 34]})
    assert not is_penalty({"type": "foul", "start": [30, 34]})
    assert not is_penalty({"type": "foul"})            # Review Focus 3: no start → False, no crash
def test_equalise_yes_when_level(): ...                # 1–0, trailing team scores → "yes"
def test_next_pass_long_no_pass_is_short(): ...        # player template, no pass in window → "short"
def test_events_before_start_ignored(): ...            # goal at t == start_t is not counted
```

- [ ] **Step 2: Run** `pytest backend/tests/test_primitives.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `primitives.py`.
- [ ] **Step 4: Run** `pytest backend/tests/test_primitives.py -v`. Expected: PASS.
- [ ] **Step 5: Commit** `[backend] Add resolver primitives: count, compare, first_of, final_state, next_matching`

---

### Task 3: Priors and Poisson pricing

**Files:**
- Create: `backend/model/priors.py`, `backend/model/poisson.py`
- Modify: `backend/tests/test_bank_loader.py` (remove the skip)
- Test: `backend/tests/test_poisson.py`

**Interfaces:**
- Produces, in `priors.py` (values copied from spec §7):
  - `MATCH_RATES_90 = {"goal": 2.7, "shot": 25, "shot_on_target": 9, "card": 4, "penalty": 0.3, "foul": 22, "save": 6}`
  - `TEAM_GOALS_90 = 1.35`
  - `POSITION_RATES_90`: the per-position table from spec §7, keys `touch`, `save`, `clearance`, `pass_complete`, `shot`, `win_ball`, `goal`. A "—" in the table means 0.
  - `INSTANT_DEFAULTS`:
    - `next_pass_dir {backward .44, forward .41, sideways .15}`
    - `keep_ball {yes .7, no .3}`
    - `reach_final_third {yes .3, no .7}`
    - `shot_soon {yes .2, no .8}`
    - `next_pass_complete {yes .8, no .2}`
    - `next_pass_long {long .25, short .75}`
    - `switch_play {yes .3, no .7}`
    - `back_to_keeper {yes .3, no .7}`
  - `PLAYER_NEXT_PASS = {"complete": {"GK": .75, "DF": .85, "MF": .82}, "long": {"GK": .55, "DF": .30, "MF": .25}}`
- Produces, in `poisson.py`:
  - `p_at_least(rate90: float, minutes: float, n: int) -> float`
  - `p_first(rate_a90: float, rate_b90: float, minutes: float) -> dict[str, float]` with keys `a`, `b`, `none`
  - `p_more(rate_a90: float, rate_b90: float, minutes: float) -> float`: P(A > B), summing k up to 30.
  - `p_final(predicate: str, score: dict[str, int], team: str | None, other: str | None, minutes_left: float) -> float`: goal grid up to 10 goals per team at `TEAM_GOALS_90`.
  - `tune_player(position: str, stat: str, mode: Literal["window_s", "line"], minutes_left: float) -> tuple[int, float]`:
    - `window_s`: searches the grid 30..600 in steps of 30, then 900, 1200, 1500, 1800 (capped at `minutes_left × 60`) for P(≥1) closest to 0.5.
    - `line`: searches 1..40 over `minutes_left` for P(≥line) closest to 0.5.
    - Returns `(value, p_yes)`.
  - `PRICERS: dict[str, Callable[[Template, dict, MatchState, Player | None], dict[str, float]]]` with keys `poisson_count`, `poisson_first`, `poisson_compare`, `goal_grid`, `player`, `instant`. Each returns a probability per option id.

- [ ] **Step 1: Write the failing tests**

```python
def test_goal_10_minutes_is_26_percent():
    assert p_at_least(2.7, 10, 1) == pytest.approx(0.259, abs=0.002)
def test_penalty_rest_of_match_from_10min():
    assert p_at_least(0.3, 80, 1) == pytest.approx(0.234, abs=0.002)
def test_first_of_sums_to_one(): ...
def test_kickoff_team_win_about_37_percent():
    assert p_final("team_wins", {"H": 0, "A": 0}, "H", "A", 90) == pytest.approx(0.37, abs=0.02)
def test_three_nil_win_is_near_certain():
    assert p_final("team_wins", {"H": 3, "A": 0}, "H", "A", 20) > 0.95
def test_gk_touch_window_tunes_near_half():
    w, p = tune_player("GK", "touch", "window_s", 45)
    assert w in (90, 120) and 0.4 <= p <= 0.6
def test_each_position_has_two_player_templates_in_band_at_10min(): ...   # touch_window always among them
def test_slow_stats_use_long_windows():
    w, p = tune_player("FW", "shot", "window_s", 35)
    assert w == 1500 and 0.4 <= p <= 0.6
```

- [ ] **Step 2: Run** `pytest backend/tests/test_poisson.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `priors.py` and `poisson.py`, then remove the skip in `test_bank_loader.py`.
- [ ] **Step 4: Run** `pytest backend/tests/test_poisson.py backend/tests/test_bank_loader.py -v`. Expected: PASS, no skips.
- [ ] **Step 5: Commit** `[backend] Add Poisson pricing and position priors for question bank`

---

### Task 4: Guards

**Files:**
- Create: `backend/bank/guards.py`
- Test: `backend/tests/test_guards.py`

**Interfaces:**
- Consumes: `Template`, `MatchState`, `Player`, `Window` (Task 1); `settle`, `Ctx` (Task 2).
- Produces:
  - `Guard = Callable[[Template, dict, MatchState, Player | None, dict[str, float]], bool]`. Its arguments are template, params, state, player and option probabilities; it returns True if the question may be asked.
  - `GUARDS: dict[str, Guard]` with keys `fits_clock`, `not_decided`, `prob_band`, `no_repeat`, `max_open_windows`, `player_active`, `position_fit`, `needs_lead_of_one`, `p_none_below_0.35`
  - `window_for(template: Template, params: dict, state: MatchState, lock_at: float) -> Window`: `counts_from` sets `start_t` (lock → `lock_at`, half_start → 0 or 2700, kickoff → 0).
  - `band_for(template: Template) -> tuple[float, float] | None`: None when `v2_no_band`.

Rules, as in spec §5, with these values pinned:
- `fits_clock`: for `Nm`/`window_s`, `state.t + 6 + seconds ≤ half_end`; for `half`, `t ≤ half_end − 600`; for `match`, `t ≤ 4500`.
- `prob_band` uses the max over options for 3-outcome templates, after top-2.
- `player_active`: the player has an event with `t ≥ state.t − 600`, and no `card` event with outcome `red` for that player.
- `max_open_windows`: `open_window_count < 2`.
- `not_decided`: calls `settle(..., closed=False)` on `state.events`; it passes when the result is `None`.

- [ ] **Step 1: Write the failing tests**

```python
def test_10m_window_at_40min_rejected(): ...                # Review Focus 1: t=2400, "10m" → False
def test_match_window_after_75min_rejected(): ...           # t=4560 → False
def test_goals_over_2_skipped_after_2_goals(): ...          # not_decided → False
def test_three_nil_team_wins_fails_normal_band(): ...
def test_penalty_rare_band_passes_match_window_early_only():   # 10:00 match → True; 30:00 match → False
def test_player_band_rejects_p_0_33(): ...
def test_sent_off_player_inactive(): ...
def test_gk_not_offered_shot_template(): ...                # position_fit
def test_equaliser_needs_lead_of_one(): ...                 # 0–0 → False; 1–0 → True; 2–0 → False
def test_no_repeat_last_and_open(): ...
def test_v2_instant_has_no_band(): ...                      # band_for(next_pass_dir) is None
```

- [ ] **Step 2: Run** `pytest backend/tests/test_guards.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `guards.py`.
- [ ] **Step 4: Run** `pytest backend/tests/test_guards.py -v`. Expected: PASS.
- [ ] **Step 5: Commit** `[backend] Add question bank guards`

---

### Task 5: Candidate building

**Files:**
- Create: `backend/bank/candidates.py`
- Test: `backend/tests/test_candidates.py`

**Interfaces:**
- Consumes: Tasks 1–4.
- Produces: `build_candidates(kind: Kind, state: MatchState, bank: dict[str, Template], open_at: float, player: Player | None = None) -> list[Candidate]`

Steps per template of that kind (with `position_fit` checked first for player templates):
1. **Expand params.** Each window value, and each team for `team_from: each`. `possession` and `trailing` resolve to one team; skip the template if that team is None.
2. **Resolve tuned values** via `tune_player` (player templates) or a line search 1..8 with `p_at_least` (`fouls_over_window`, `goals_over`, counting goals already scored).
3. **Price** with `PRICERS[template.prob]`.
4. **Pick options.** For 3-outcome templates keep the top 2 and renormalise `p` over them.
5. **Run guards** in the template's `guards` order, stopping at the first failure.
6. **For rare templates,** keep only the shortest window that passes.
7. **Fill the text.**

Result fields: `resolve_by` is `open_at + 6 + seconds` for timed windows, `"HT"` or `"FT"` for half and match, and `open_at + 6 + 15` for instant (`next_*`) and `+ instant window` otherwise.

- [ ] **Step 1: Write the failing tests**

```python
def test_dull_midfield_at_20min_has_instant_window_player_candidates(): ...
def test_window_candidates_at_80min_exclude_20m_and_match(): ...
def test_next_pass_dir_shows_top2_renormalised():
    c = only(build_candidates("instant", state, bank, 1200), "next_pass_dir")
    assert c.options == ["backward", "forward"] and c.p["backward"] == pytest.approx(0.44 / 0.85, abs=1e-3)
def test_penalty_offered_once_with_shortest_passing_window(): ...
def test_goalkeeper_gets_only_gk_templates(): ...
def test_candidate_category_mapping(): ...   # window → "forecast"
```

- [ ] **Step 2: Run** `pytest backend/tests/test_candidates.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `candidates.py`.
- [ ] **Step 4: Run** `pytest backend/tests/test_candidates.py -v`. Expected: PASS.
- [ ] **Step 5: Commit** `[backend] Add question bank candidate builder`

---

### Task 6: Slot selection + design doc updates

**Files:**
- Create: `backend/bank/select.py`
- Modify: `docs/pipeline.md`:
  - §[2b]: replace the "Hard rules" table rows for Forecast, Favourite-player forecast and Favourite-player instant with "All kinds come through dull slots; kind drawn at random, see `backend/bank/`". Delete the "Kickoff and half time" stack paragraph.
  - §[2]: above the catalog table, add "Source of truth: `backend/bank/templates.json` (28 templates); this table lists the v2 originals."
- Test: `backend/tests/test_select.py`

**Interfaces:**
- Consumes: `build_candidates` (Task 5).
- Produces:
  - `Picker = Callable[[list[Candidate], MatchState], str | None]`: returns a `Candidate.key`, or None.
  - `slot_rng(match_id: str, open_at: float) -> random.Random`
  - `select_for_slot(state: MatchState, open_at: float, users: dict[str, Player], bank: dict[str, Template], picker: Picker | None = None, timeout_s: float = 2.0) -> SlotDecision | None`

Algorithm (spec §6):
1. Draw `kind = rng.choice(["instant", "window", "player"])`.
2. **Player kind.** For each user, build candidates for their player. If a user has none, retry with `player_touch_window` for the other players with the same position on the same team, in roster order. If any user still has none, set kind to instant. Otherwise, choose per user with the same `rng` in sorted `user_id` order and return `per_user`.
3. **Window kind with no candidates** → instant.
4. **Instant kind with no candidates** → return None.
5. **Shared kinds.** Call `picker` inside a `ThreadPoolExecutor` with `timeout_s`. Use its key if it matches a candidate. Otherwise (None, unknown key, exception or timeout) use `rng.choice(candidates)`.

- [ ] **Step 1: Write the failing tests**

```python
def test_same_match_and_second_same_decision(): ...           # deterministic, no picker
def test_kind_draw_roughly_uniform_over_300_slots(): ...      # each kind 25–42%
def test_player_slot_falls_back_to_teammate_when_sent_off(): ...   # Review Focus 4
def test_player_slot_goes_instant_when_one_user_has_nothing(): ... # Review Focus 4
def test_picker_key_used_when_valid(): ...
def test_picker_unknown_key_raises_or_hangs_falls_back():     # Review Focus 5
    for bad in (lambda c, s: "nope", boom, lambda c, s: time.sleep(5)):
        t0 = time.monotonic()
        d = select_for_slot(state, 1200, users, bank, picker=bad, timeout_s=0.2)
        assert d.shared.key == select_for_slot(state, 1200, users, bank).shared.key
        assert time.monotonic() - t0 < 1.0
def test_late_match_window_draw_falls_back_to_instant(): ...  # t=4560, only instant passes
```

- [ ] **Step 2: Run** `pytest backend/tests/test_select.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `select.py`, then edit `docs/pipeline.md` as listed above.
- [ ] **Step 4: Run** `pytest backend/tests -v`. Expected: all PASS.
- [ ] **Step 5: Commit** `[backend] Add slot selection for question bank; Docs pipeline: all questions via dull slots`
- [ ] **Step 6:** Push `backend/question-bank` and open a PR with the CLAUDE.md checklist (stages [2] [2b] [3] [4], tier P1). Tell CWT96 that the kickoff/half-time stack UI is no longer needed.

---

## Not in this plan (follow-ups)

- Wiring `select_for_slot` into the timing agent `[2b]` and the app's replay loop. That depends on `Detector` and `app/`, which are P0 work in progress.
- The real question-generator LLM prompt (`agents/prompts/`) that implements `Picker`, plus its cache entries.
- Replacing `INSTANT_DEFAULTS` with v2's frequency tables once they land in `backend/model/`.
- Fictional player profiles (the next knowledge base).
