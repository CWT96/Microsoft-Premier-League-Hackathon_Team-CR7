"""test_model.py — tests for the probability model [3]: tables, API, fair lines, calibration.

Last Modified: 2026-10-24 23:10, first version.
Inputs:  hand-built matches; data/source/sim-placeholder (MODEL_EVAL_DATA_ROOT overrides the repo root)
Outputs: none

Workflow:
  - unit tests on a small hand-built match (sum to 1, top2, hint, registry, determinism)
  - dataset tests (run on the repo dataset): fair lines for every player, calibration

CAUTION: MODEL_EVAL_DATA_ROOT, if set, points at a repo root that holds data/source
checkout, i.e. the directory that holds ``data/source`` is ``<root>``.
"""

from __future__ import annotations

import math
import os
from pathlib import Path

import pytest

from backend.core.types import DetectorState, Match, MatchEvent, Player, Roster
import threading

from backend.model import (fair_line, fit, hint, option_probs, register_prob, top2)
from backend.model import model as model_mod
from backend.model.model import cache_model, clear_cache, get_model
from backend.model import registry as prob_registry
from backend.model.model import iter_instant_samples

N, C = "NBR", "CAL"
BINARY = ("keep_ball", "reach_final_third", "shot_soon", "goals_over", "team_wins",
          "card_second_half", "second_half_goal")

# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
#
# Hand-built match
#
# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%


def make_roster() -> Roster:
    players = (
        Player("nbr-1", "Ward", N, "GK", 1),
        Player("nbr-5", "Stone", N, "DF", 5),
        Player("nbr-8", "Mensah", N, "MF", 8),
        Player("nbr-9", "Okoro", N, "FW", 9),
        Player("nbr-20", "Bench", N, "MF", 20, starter=False),
        Player("cal-1", "Fox", C, "GK", 1),
        Player("cal-9", "Reyes", C, "FW", 9),
    )
    return Roster(home=N, away=C, home_name="Northbridge", away_name="Calder", players=players)


def make_match() -> Match:
    """~300 passes with a mix of directions, some turnovers, shots, saves, a goal and a card."""
    evs: list[MatchEvent] = []
    n = 0

    def add(t, type_, team=N, half=1, **kw):
        nonlocal n
        n += 1
        evs.append(MatchEvent(id=f"e{n:04d}", t=t, half=half, team=team, type=type_, **kw))

    add(0.0, "kickoff", start=(52.5, 34.0))
    t = 2.0
    for half, base in ((1, 0.0), (2, 2700.0)):
        for i in range(150):
            t = base + 5.0 + i * 17.0
            dx = (12.0, 0.5, -9.0)[i % 3]
            x0 = 20.0 + (i * 7) % 70
            pid = ("nbr-5", "nbr-8", "nbr-9", "nbr-8")[i % 4]
            add(t, "pass", N, half, player_id=pid, start=(x0, 30.0),
                end=(x0 + dx, 31.0), outcome="complete" if i % 5 else "incomplete")
            if i % 11 == 10:
                add(t + 3.0, "interception", C, half, player_id="cal-9", start=(60.0, 30.0))
            if i % 13 == 12:
                add(t + 2.0, "shot", N, half, player_id="nbr-9", start=(90.0, 34.0),
                    end=(105.0, 35.0), outcome="saved")
                add(t + 3.0, "save", C, half, player_id="cal-1", start=(105.0, 35.0))
    add(3000.0, "goal", N, 2, player_id="nbr-9", start=(105.0, 34.0), outcome="goal")
    add(3100.0, "card", C, 2, player_id="cal-9", outcome="yellow")
    add(2790.0, "half_end", N, 1)
    add(5500.0, "full_end", N, 2)
    evs.sort(key=lambda e: (e.half, e.t, e.id))
    return Match("test", make_roster(), tuple(evs), None, 2790.0, 5500.0)


def make_state(**kw) -> DetectorState:
    base = dict(t=600.0, half=1, mode="live", break_kind=None, score_home=0, score_away=0,
                situation="open_play", dull_score=0.5, threat=0.05, movement=None,
                pressure_m=None, ball_xy=(50.0, 34.0), ball_zone="middle_third",
                possession_team=N, seconds_since_shot=100.0, last_attack_end_t=None,
                attack_active=False, restart_pending=False, restart_taken=False,
                dull_parts={"possession": 0.5})
    base.update(kw)
    return DetectorState(**base)


@pytest.fixture(scope="module")
def model():
    return fit(make_match())


# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
#
# Unit tests
#
# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

PARAMS = {
    "goals_over": {"line": 2.5}, "team_wins": {"team": N}, "player_stat_over": {
        "player_id": "nbr-9", "line": 1}, "player_touch_window": {"player_id": "nbr-8",
                                                                   "window_s": 60},
}


def test_option_probs_sum_to_one_and_inside_open_interval(model):
    zones = ("own_third", "middle_third", "final_third")
    for tid in ("next_pass_dir",) + BINARY + ("player_stat_over", "player_touch_window"):
        for zone in zones:
            for half, t in ((1, 10.0), (1, 2600.0), (2, 4000.0), (2, 5399.0)):
                st = make_state(ball_zone=zone, half=half, t=t)
                p = option_probs(tid, st, PARAMS.get(tid, {}), model=model)
                assert sum(p.values()) == pytest.approx(1.0)
                assert all(0.0 < v < 1.0 for v in p.values()), (tid, p)


def test_next_pass_dir_has_three_outcomes(model):
    p = option_probs("next_pass_dir", make_state(), {}, model=model)
    assert set(p) == {"forward", "sideways", "backward"}


def test_binary_templates_use_yes_no(model):
    for tid in BINARY:
        p = option_probs(tid, make_state(), PARAMS.get(tid, {}), model=model)
        assert set(p) == {"yes", "no"}


def test_next_pass_dir_follows_the_training_data(model):
    # Deep in own third the synthetic passes are mostly forward-and-back in equal measure.
    p = option_probs("next_pass_dir", make_state(ball_zone="own_third"), {}, model=model)
    assert max(p.values()) < 0.7


def test_top2_returns_two_most_likely_renormalised(model):
    options, probs = top2("next_pass_dir", make_state(), {}, model=model)
    full = option_probs("next_pass_dir", make_state(), {}, model=model)
    assert len(options) == 2 and sum(probs) == pytest.approx(1.0)
    assert set(options) == set(sorted(full, key=full.get, reverse=True)[:2])
    assert probs[0] >= probs[1]


def test_top2_of_binary_template_keeps_both(model):
    options, probs = top2("keep_ball", make_state(), {}, model=model)
    assert set(options) == {"yes", "no"} and sum(probs) == pytest.approx(1.0)


def test_hint_renormalises_over_shown_options():
    pick, p = hint({"forward": 0.30, "backward": 0.10})
    assert pick == "forward" and p == pytest.approx(0.75)


def test_hint_tie_is_deterministic():
    assert hint({"yes": 0.5, "no": 0.5})[0] == "no"


def test_hint_rejects_wrong_size():
    with pytest.raises(ValueError):
        hint({"a": 1.0})


def test_unknown_template_raises(model):
    with pytest.raises(ValueError):
        option_probs("nope", make_state(), {}, model=model)


def test_goals_over_moves_with_score_and_clock(model):
    early = option_probs("goals_over", make_state(t=60.0), {"line": 2}, model=model)["yes"]
    late_nil = option_probs("goals_over", make_state(t=5300.0), {"line": 2}, model=model)["yes"]
    late_two = option_probs("goals_over", make_state(t=5300.0, score_home=2),
                            {"line": 2}, model=model)["yes"]
    assert late_nil < early < late_two


def test_team_wins_follows_score(model):
    lead = option_probs("team_wins", make_state(t=4800.0, score_home=2), {"team": N},
                        model=model)["yes"]
    trail = option_probs("team_wins", make_state(t=4800.0, score_away=2), {"team": N},
                         model=model)["yes"]
    assert lead > 0.9 and trail < 0.1


def test_registry_extension_and_duplicate_guard(model):
    tid = "test_extra_template"
    try:
        register_prob(tid, lambda m, s, p: {"yes": 0.7, "no": 0.3})
        assert option_probs(tid, make_state(), {}, model=model) == {"yes": 0.7, "no": 0.3}
        with pytest.raises(ValueError):
            register_prob(tid, lambda m, s, p: {})
    finally:
        prob_registry.PROBS.pop(tid, None)


def test_fit_is_deterministic():
    a, b = fit(make_match()), fit(make_match())
    st = make_state()
    for tid in ("next_pass_dir", "keep_ball", "shot_soon"):
        assert option_probs(tid, st, {}, model=a) == option_probs(tid, st, {}, model=b)
    assert fair_line("player_stat_over", "nbr-9", st, model=a) == fair_line(
        "player_stat_over", "nbr-9", st, model=b)


def test_fair_line_contract_shape_and_guardrail(model):
    for pid in make_roster().by_id():
        out = fair_line("player_stat_over", pid, make_state(t=0.0, mode="lobby"), model=model)
        assert out["template_id"] in ("player_stat_over", "player_touch_window")
        assert 0.4 <= out["p"] <= 0.6
        if out["template_id"] == "player_stat_over":
            assert isinstance(out["params"]["line"], int) and out["params"]["line"] >= 1
        else:
            assert 30 <= out["params"]["window_s"] <= 300


def test_fair_line_touch_picks_window_closest_to_half(model):
    out = fair_line("player_touch_window", "nbr-8", make_state(), model=model)
    assert out["template_id"] == "player_touch_window"
    assert abs(out["p"] - 0.5) <= 0.1


def test_fair_line_unknown_player_raises(model):
    with pytest.raises(ValueError):
        fair_line("player_stat_over", "ghost", make_state(), model=model)


def test_missing_state_fields_fall_back(model):
    st = make_state(ball_zone=None, ball_xy=None, dull_parts={})
    p = option_probs("keep_ball", st, {}, model=model)
    assert sum(p.values()) == pytest.approx(1.0)


def test_special_ids_do_not_break(model):
    assert fair_line("player_touch_window", "nbr-20", make_state(), model=model)["p"] > 0


def test_lobby_and_break_states_never_raise(model):
    for st in (make_state(mode="lobby", half=0, t=0.0),
               make_state(mode="break", break_kind="half_time", half=1, t=2790.0),
               make_state(mode="full_time", half=3, t=5600.0),
               make_state(mode="break", break_kind="water_break", half=2, t=4000.0)):
        out = fair_line("player_stat_over", "nbr-9", st, model=model)
        assert "in_range" in out
        assert option_probs("player_stat_over", st, {"player_id": "nbr-9", "line": 1},
                            model=model)["yes"] > 0


def test_break_means_whole_next_half(model):
    mid = make_state(t=2600.0)  # 100 s left of half 1
    ht = make_state(mode="break", break_kind="half_time", t=2790.0)
    args = {"player_id": "nbr-9", "line": 1}
    assert (option_probs("player_stat_over", ht, args, model=model)["yes"]
            > option_probs("player_stat_over", mid, args, model=model)["yes"])


def test_possession_share_reaches_pass_bucket_level(model):
    from backend.model.probs import _pass_levels
    assert _pass_levels(make_state(dull_parts={"possession": 0.5}))[-1] == ("middle_third", "mid")
    assert len(_pass_levels(make_state(dull_parts={}))) == 2


def test_next_pass_dir_sums_to_one_after_clamp(model):
    p = option_probs("next_pass_dir", make_state(), {}, model=model)
    assert sum(p.values()) == pytest.approx(1.0, abs=1e-12)


def test_hint_tie_uses_same_key_as_top2():
    probs = {"sideways": 0.5, "backward": 0.5}
    assert hint(probs)[0] == "backward"


def test_player_never_on_pitch_has_sane_rates_and_flag(model):
    assert model.players["nbr-20"].seconds_on == 0.0
    out = fair_line("player_touch_window", "nbr-20", make_state(), model=model)
    assert out["in_range"] is (0.4 <= out["p"] <= 0.6)
    assert model.touch_rate["nbr-20"] > 0


def test_out_of_range_fair_line_is_flagged_and_logged(model, caplog):
    import dataclasses
    dead = dataclasses.replace(model, touch_rate={**model.touch_rate, "nbr-9": 1e-6})
    with caplog.at_level("WARNING"):
        out = fair_line("player_touch_window", "nbr-9", make_state(), model=dead)
    assert out["in_range"] is False and "nbr-9" in caplog.text


def test_get_model_loads_once_under_threads(monkeypatch):
    import sys, types
    calls = []

    def fake_load(match_id, with_tracking=False):
        calls.append(match_id)
        return make_match()

    monkeypatch.setitem(sys.modules, "backend.ingest",
                        types.SimpleNamespace(load_match=fake_load))
    clear_cache()
    try:
        out = []
        threads = [threading.Thread(target=lambda: out.append(get_model("test")))
                   for _ in range(8)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        assert len(calls) == 1 and len({id(m) for m in out}) == 1
        clear_cache()
        get_model("test")
        assert len(calls) == 2
    finally:
        clear_cache()


# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
#
# Dataset tests
#
# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = Path(os.environ.get("MODEL_EVAL_DATA_ROOT", REPO_ROOT))
CALIBRATION_TOL = 0.15


@pytest.fixture(scope="module")
def dataset() -> Match:
    """The placeholder match (repo data by default; MODEL_EVAL_DATA_ROOT overrides)."""
    from backend.ingest import load_match

    source = DATA_ROOT / "data/source"
    if not (source / "sim-placeholder").exists():
        pytest.skip(f"no dataset under {source}")
    return load_match("nbr-cal-seed42", root=source, with_tracking=False)


def test_dataset_fair_lines_for_every_player(dataset):
    m = fit(dataset)
    rows = []
    for p in dataset.roster.players:
        for half_state in (make_state(t=0.0, mode="lobby"),
                           make_state(t=2790.0, mode="break", break_kind="half_time")):
            out = fair_line("player_stat_over", p.player_id, half_state, model=m)
            assert 0.4 <= out["p"] <= 0.6, (p.player_id, out)
            rows.append((p.position, p.player_id, out))
    if os.environ.get("MODEL_PRINT_TABLE"):
        for pos, pid, out in rows:
            print(pos, pid, out["template_id"], out["params"], round(out["p"], 3))


def test_dataset_calibration_matches_empirical_rates(dataset):
    m = fit(dataset)
    samples = [s for s in iter_instant_samples(dataset) if s.keep is not None]
    assert len(samples) > 200
    for tid, attr in (("keep_ball", "keep"), ("reach_final_third", "final3"),
                      ("shot_soon", "shot")):
        pairs = [(s, getattr(s, attr)) for s in samples if getattr(s, attr) is not None]
        emp = sum(1 for _, y in pairs if y) / len(pairs)
        pred = sum(option_probs(tid, make_state(
            ball_zone=s.zone, dull_parts={"possession": min(s.passes / 6, 1.0)},
            threat=0.2 if s.high_threat else 0.0), {}, model=m)["yes"]
            for s, _ in pairs) / len(pairs)
        assert abs(pred - emp) <= CALIBRATION_TOL, (tid, pred, emp)
    assert math.isclose(sum(option_probs("next_pass_dir", make_state(), {}, model=m).values()), 1.0)
