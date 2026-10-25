"""test_timing_sim.py — timing simulator: targets, locks, determinism, user ordering.

Last Modified: 2026-10-24 23:59, first version
Inputs:  a hand-built mini match (always) and the dataset under TIMING_SIM_DATA_ROOT (skippable)
Outputs: none

Workflow:
  - assert invariants and targets on the mini match and, when present, the real dataset

CAUTION: dataset numbers are never hard-coded; only targets and invariants are asserted.
"""

from __future__ import annotations

import pytest

from backend.agents import timing_sim as sim
from backend.core.constants import DEFAULT_MATCH_ID
from backend.core.types import Match, MatchEvent, Roster

ROSTER = Roster("NBR", "CAL", "Northbridge", "Calder", ())
_n = iter(range(1, 100000))


def ev(t, team, typ, start=None, end=None, half=1, outcome=None):
    return MatchEvent(id=f"e{next(_n):05d}", t=t, half=half, team=team, type=typ, start=start,
                      end=end, outcome=outcome)


def mini_match() -> Match:
    evs = [ev(0.0, "NBR", "kickoff", (52.5, 34))]
    evs += [ev(5.0 + 3 * i, "NBR", "pass", (30, 34), (36, 34), outcome="complete")
            for i in range(25)]  # dull back-field passing to t=77
    evs += [ev(80.0, "CAL", "throw_in", (50, 0)), ev(83.0, "CAL", "pass", (50, 0), (56, 6))]
    evs += [ev(100.0 + 2 * i, "NBR", "pass", (72 + 6 * i, 34), (78 + 6 * i, 34),
               outcome="complete") for i in range(4)]
    evs += [ev(110.0, "NBR", "shot", (95, 34), (105, 34)), ev(111.0, "CAL", "save", (105, 34)),
            ev(132.0, "NBR", "corner", (104, 0)), ev(140.0, "NBR", "pass", (104, 0), (90, 20)),
            ev(160.0, "NBR", "half_end")]
    evs += [ev(2700.0 + 3 * i, "CAL", "pass", (30, 34), (36, 34), half=2, outcome="complete")
            for i in range(20)]
    evs += [ev(2700.0, "CAL", "kickoff", (52.5, 34), half=2),
            ev(2780.0, "CAL", "full_end", half=2)]
    return Match("mini", ROSTER, tuple(sorted(evs, key=lambda e: (e.half, e.t, e.id))), None,
                 first_half_end_t=160.0, full_end_t=2780.0)


@pytest.fixture(scope="module")
def mini_report():
    return sim.simulate(mini_match())


def dataset_match():
    root = sim.data_root()
    try:
        from backend.ingest.loader import load_match
    except ImportError:
        pytest.skip("ingest loader not available")
    if not root.exists():
        pytest.skip(f"no dataset under {root}")
    try:
        return load_match(DEFAULT_MATCH_ID, root=root)
    except FileNotFoundError:
        pytest.skip("default match not in dataset")


@pytest.fixture(scope="module")
def data_report():
    return sim.simulate(dataset_match())


def check_invariants(rep):
    assert rep.fast_attack_moments == 0
    assert rep.post_kick_answers == 0
    users = rep.users
    assert users["fast"].answered >= users["normal"].answered >= users["hesitant"].answered
    assert users["drifting"].nudges >= 1
    assert users["normal"].seen > 0 and len(rep.moments) > 0
    assert 0.0 <= rep.cut_short_share <= 1.0 and 0.0 <= rep.calm_share <= 1.0


def test_mini_match_invariants(mini_report):
    check_invariants(mini_report)


def test_mini_match_has_stacks_and_calm_feed(mini_report):
    kinds = {m.kind for m in mini_report.moments}
    assert {"kickoff_stack", "ht_stack", "instant"} <= kinds
    assert mini_report.calm_moments_per_min > 0


def test_mini_match_is_deterministic():
    a, b = sim.simulate(mini_match()), sim.simulate(mini_match())
    assert sim.render_report(a) == sim.render_report(b)
    assert a.moments == b.moments


def test_seed_changes_user_behaviour_only():
    a, b = sim.simulate(mini_match(), seed=1), sim.simulate(mini_match(), seed=2)
    assert a.moments == b.moments


def test_attack_locks_open_cards_early():
    rep = sim.simulate(mini_match())
    assert sum(u.cut_short for u in rep.users.values()) >= 1  # cards open when 100 s attack began
    assert rep.fast_attack_moments == 0


def test_set_piece_cards_lock_at_the_kick():
    rep = sim.simulate(mini_match())
    sp = [m for m in rep.moments if m.situation == "set_piece_wait"]
    assert sp
    assert all(m.lock_rule == "on_restart" for m in sp)
    assert rep.post_kick_answers == 0


def test_report_renders_and_writes(tmp_path, mini_report):
    path = sim.write_report(mini_report, tmp_path / "out" / "timing_report.md")
    text = path.read_text()
    assert "| Metric | Target | Actual | Status |" in text
    assert "## Moments by situation" in text and "PASS" in text
    assert all(sit in text for sit in mini_report.moments_by_situation)


def test_targets_table_has_every_spec_metric(mini_report):
    names = [t.name for t in mini_report.targets()]
    assert len(names) == 7 and names[-1].startswith("Share of match time")


# .............................................................................
# dataset (skipped when absent)
# .............................................................................


def test_dataset_invariants(data_report):
    check_invariants(data_report)


def test_dataset_hard_targets(data_report):
    by = {t.name: t for t in data_report.targets()}
    assert by["Moments opened during a fast attack"].passed
    assert by["Set-piece cards answered after the kick"].passed


def test_dataset_deterministic(data_report):
    again = sim.simulate(dataset_match())
    assert sim.render_report(again) == sim.render_report(data_report)


def test_typical_cards_is_a_quarter_of_the_normal_users_answers(data_report):
    dataset_match()  # skip when absent
    n = sim.typical_cards()
    assert n == round(sim.TYPICAL_ENGAGEMENT * data_report.users["normal"].answered) and n > 0
    assert sim.typical_cards() == n  # cached


def test_cli_writes_report(tmp_path):
    dataset_match()
    out = tmp_path / "r.md"
    assert sim.main(["--out", str(out), "--root", str(sim.data_root())]) == 0
    assert "## Targets" in out.read_text()
