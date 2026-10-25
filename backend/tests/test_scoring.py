"""test_scoring.py — tests for backend/scoring (positive scoring formula, radar, ranks).

Last Modified: 2026-10-24 23:00, review round 1: stronger order, hint, rank and radar tests.
Inputs:  none
Outputs: none

Workflow:
  - Worked example, streak/hint/void rules, tiers, ranks, radar, property sweeps, contract.

CAUTION: Correctness comes only from the chosen option: option "A" is right, "B" is wrong.
"""

from __future__ import annotations

from itertools import product
from typing import get_args

import pytest

from backend.app.models import FinalScore, dump
from backend.core.types import Answer, MomentSituation, Outcome
from backend.scoring import best_moment, final_score
from backend.scoring.scoring import (
    TYPICAL_POINTS_PER_CARD,
    _largest_remainder,
    earned_points,
    tier_for,
)
from backend.tests.contract_utils import validate


def ans(qid, correct=True, cat="instant", sit="calm_play", open_at=None, hint=False,
        lat=2.0, answered=True, half=1):
    """One shown card. Option "A" is the right answer, "B" the wrong one."""
    return Answer(qid=qid, category=cat, situation=sit,
                  open_at=float(open_at if open_at is not None else int(qid[1:]) * 10),
                  lock_at=0.0, half=half,
                  option=("A" if correct else "B") if answered else None,
                  answer_latency_s=lat if answered else None, used_hint=hint)


def oc(answers, void=(), missing=()):
    """Outcomes for every answer: right option "A"; void and missing qids as requested."""
    return {a.qid: Outcome(a.qid, "void", None, ()) if a.qid in void
            else Outcome(a.qid, "resolved", "A", ())
            for a in answers if a.qid not in missing}


def run(answers, outcomes=None, n_typical=40, **kw):
    outcomes = oc(answers) if outcomes is None else outcomes
    return final_score("u", "m", answers, outcomes, n_typical, **kw)


# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
#
# Points, streaks, final formula
#
# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%


def test_worked_example():
    """Hand-computed.

    q1 instant correct, streak 1: 10 + 20*1.0       = 30
    q2 instant correct, hint, streak 2: 10 + 20*1.2*.5 = 22
    q3 forecast correct, streak 3: 10 + 20*1.5      = 40
    q4 instant wrong: 10, streak resets
    q5 void: skipped; q6 player unanswered: skipped (but issued)
    q7 player correct, streak 1: 30
    earned = 132 over 5 answered cards -> rate 26.4 >= 22.8 -> quality 1
    n_typical = 10 -> participation = sqrt(5/10) = 0.7071
    final = round(600*1 + 400*0.7071) = round(882.8) = 883 -> Super Fan
    split 883 by earned (62/40/30): 414.7, 267.6, 200.7 -> 415, 267, 201
    """
    answers = [
        ans("q1"), ans("q2", hint=True), ans("q3", cat="forecast", sit="kickoff"),
        ans("q4", correct=False), ans("q5"), ans("q6", cat="player", answered=False),
        ans("q7", cat="player"),
    ]
    fs = run(answers, oc(answers, void=("q5",)), n_typical=10)
    assert earned_points(answers, oc(answers, void=("q5",))) == 132
    assert fs.final_score == 883
    assert fs.tier == "Super Fan"
    assert (fs.issued, fs.answered, fs.correct) == (6, 5, 4)
    assert fs.hit_rate == pytest.approx(0.8)
    assert fs.best_streak == 3 and fs.hints_used == 1
    bc = fs.by_category
    assert (bc.instant.points, bc.forecast.points, bc.player.points) == (415, 267, 201)
    assert (bc.instant.issued, bc.instant.correct) == (3, 2)
    assert (bc.player.issued, bc.player.correct) == (2, 1)


def test_typical_points_per_card_is_cycle_average():
    cycle = (10 + 20 * 1.0) + (10 + 20 * 1.2) + 10 + (10 + 20 * 1.0) + 10  # C C W C W
    assert TYPICAL_POINTS_PER_CARD == pytest.approx(cycle / 5)


def test_streak_multipliers_and_cap():
    answers = [ans(f"q{i}") for i in range(1, 6)]
    assert earned_points(answers, oc(answers)) == 30 + 34 + 40 + 40 + 40


def test_wrong_resets_streak():
    answers = [ans("q1"), ans("q2", correct=False), ans("q3"), ans("q4")]
    assert earned_points(answers, oc(answers)) == 30 + 10 + 30 + 34


def test_unanswered_card_leaves_streak_intact():
    a = [ans("q1"), ans("q2", answered=False), ans("q3")]
    assert earned_points(a, oc(a)) == 30 + 34
    assert run(a).best_streak == 2


def test_void_card_leaves_streak_intact():
    a = [ans("q1"), ans("q2"), ans("q3")]
    o = oc(a, void=("q2",))
    assert earned_points(a, o) == 30 + 34
    assert run(a, o).best_streak == 2


def test_issue_order_qid_tiebreak_decides_streak():
    # Same half and open_at: only the qid orders them. In qid order: C C C W.
    # In list order (q1, q4, q2, q3) it would be C W C C and the streak would differ.
    a = [ans("q1", open_at=10), ans("q4", correct=False, open_at=10),
         ans("q2", open_at=10), ans("q3", open_at=10)]
    assert earned_points(a, oc(a)) == 30 + 34 + 40 + 10
    assert run(a).best_streak == 3


def test_issue_order_open_at_and_half():
    a = [ans("q2", open_at=20), ans("q1", open_at=10, correct=False)]
    assert earned_points(a, oc(a)) == 10 + 30  # wrong first, then streak restarts at 1
    # first-half stoppage (open_at 2800, half 1) comes before second half (open_at 100)
    a = [ans("q2", open_at=100, half=2), ans("q1", open_at=2800, half=1, correct=False)]
    assert earned_points(a, oc(a)) == 10 + 30


def test_hint_halves_only_bonus():
    a = [ans("q1", hint=True)]
    assert earned_points(a, oc(a)) == 10 + 20 * 0.5


def test_hint_on_wrong_answer_earns_participation_and_counts():
    a = [ans("q1", correct=False, hint=True)]
    assert earned_points(a, oc(a)) == 10
    assert run(a).hints_used == 1


def test_hint_at_streak_three_cap():
    a = [ans("q1"), ans("q2"), ans("q3", hint=True)]
    assert earned_points(a, oc(a)) == 30 + 34 + (10 + 20 * 1.5 * 0.5)


def test_void_excluded_from_issued_and_answered():
    a = [ans("q1"), ans("q2")]
    fs = run(a, oc(a, void=("q2",)))
    assert (fs.issued, fs.answered, fs.correct) == (1, 1, 1)


def test_missing_outcome_treated_as_void():
    a = [ans("q1"), ans("q2"), ans("q3")]
    fs = run(a, oc(a, missing=("q2",)))
    assert (fs.issued, fs.answered, fs.correct) == (2, 2, 2)
    assert fs.best_streak == 2


def test_no_answers():
    fs = run([ans("q1", answered=False)])
    assert (fs.final_score, fs.answered, fs.hit_rate, fs.issued) == (0, 0, 0.0, 1)
    assert fs.radar.crystal_ball is None and fs.radar.lightning_thumbs is None
    assert fs.radar.on_fire is None
    assert fs.tier == "Rookie"


def test_cap_at_1000():
    a = [ans(f"q{i}") for i in range(1, 11)]
    fs = run(a, n_typical=5)
    assert fs.final_score == 1000 and fs.tier == "Super Fan"


def test_bad_n_typical():
    with pytest.raises(ValueError):
        run([], n_typical=0)


@pytest.mark.parametrize("pts,tier", [(0, "Rookie"), (399, "Rookie"), (400, "Rising Star"),
                                      (699, "Rising Star"), (700, "Super Fan"),
                                      (1000, "Super Fan")])
def test_tier_boundaries(pts, tier):
    assert tier_for(pts) == tier


@pytest.mark.parametrize("n_typical", [3, 7, 40, 500])
def test_category_points_sum_to_final(n_typical):
    a = [ans("q1"), ans("q2", cat="forecast"), ans("q3", cat="player", correct=False),
         ans("q4", cat="player")]
    fs = run(a, n_typical=n_typical)
    bc = fs.by_category
    assert bc.instant.points + bc.forecast.points + bc.player.points == fs.final_score


@pytest.mark.parametrize("total,weights", [(10, [1, 1, 1]), (883, [62, 40, 30]), (7, [5, 0, 2]),
                                           (1000, [0.1, 0.2, 0.3])])
def test_largest_remainder_sums(total, weights):
    assert sum(_largest_remainder(total, weights)) == total


def test_largest_remainder_zero_cases():
    assert _largest_remainder(10, [0, 0, 0]) == [0, 0, 0]
    assert _largest_remainder(0, [1, 2, 3]) == [0, 0, 0]
    assert _largest_remainder(10, [1, 1, 1]) == [4, 3, 3]  # ties go to the earlier category


# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
#
# Positivity and monotonicity
#
# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%


def _cards(flags, hint=False):
    return [ans(f"q{i + 1}", correct=f, hint=hint) for i, f in enumerate(flags)]


def test_positivity():
    """10 answered, 3 correct (spread out), n_typical = 40.

    earned = 30+10+10+30+10+10+30+10+10+10 = 160 -> rate 16.0, quality 16/22.8 = 0.702
    participation = sqrt(10/40) = 0.5
    final = round(600*0.702 + 400*0.5) = round(621.1) = 621
    """
    a = _cards([True, False, False, True, False, False, True, False, False, False])
    assert run(a).final_score == 621


def test_perfect_engaged_player_gets_1000():
    assert run(_cards([True] * 40), n_typical=40).final_score == 1000


def test_more_participation_same_accuracy_never_lowers_score():
    pattern = [True, True, False, True, False]
    scores = [run(_cards(pattern * c)).final_score for c in (1, 2, 4, 8)]
    assert scores == sorted(scores) and scores[-1] > scores[0]


def test_more_participation_fixed_quality_all_wrong_sweep():
    scores = [run(_cards([False] * n)).final_score for n in range(1, 61)]
    assert scores == sorted(scores)


@pytest.mark.parametrize("n,pct,hint", list(product(range(5, 16), (0, 20, 40), (False, True))))
def test_casual_users_feel_good(n, pct, hint):
    k = n * pct // 100
    spread = {i * n // k for i in range(k)} if k else set()  # correct answers spaced apart
    a = _cards([i in spread for i in range(n)], hint=hint)
    assert run(a, n_typical=40).final_score >= 300


# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
#
# Ranks
#
# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%


def _mine():
    """One answered correct card, n_typical 4: quality 1, participation 0.5 -> 800."""
    return [ans("q1")]


def test_rank_only_when_flattering():
    fs = run(_mine(), n_typical=4, others_final=[100, 200, 900, 950],
             room_final=[850, 900, 950], friends_final=[100])
    assert fs.final_score == 800
    assert fs.rank.global_ is None  # rank 3 of 5
    assert fs.rank.room is None  # rank 4 of 4
    assert fs.rank.friends == 1  # 1 of 2


def test_rank_boundary_exactly_top_half():
    fs = run(_mine(), n_typical=4, others_final=[900, 100, 200], room_final=[900, 950, 100])
    assert fs.rank.global_ == 2  # 2 of 4 -> shown
    assert fs.rank.room is None  # 3 of 4 -> just outside


def test_rank_odd_scope_boundary():
    fs = run(_mine(), n_typical=4, others_final=[900, 100, 200, 300],
             room_final=[900, 950, 100, 200])
    assert fs.rank.global_ == 2  # 2 of 5 (<= 2.5)
    assert fs.rank.room is None  # 3 of 5


def test_rank_tie_shares_better_rank_and_all_tied_is_first():
    fs = run(_mine(), n_typical=4, others_final=[900, 800, 800, 800, 100])
    assert fs.rank.global_ == 2  # three others tie with the user, one above
    assert run(_mine(), n_typical=4, others_final=[800, 800, 100]).rank.global_ == 1
    assert run(_mine(), n_typical=4, others_final=[900, 950, 800]).rank.global_ is None


def test_rank_empty_scope_is_none():
    r = run(_mine(), n_typical=4).rank
    assert (r.global_, r.room, r.friends) == (None, None, None)


# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
#
# Radar
#
# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%


def test_radar_exact_values():
    # points: q1 W 10, q2 C 30, q3 W 10, q4 C 30; latencies 1,3,3,5 -> median 3; best streak 1
    a = [
        ans("q1", correct=False, cat="forecast", sit="kickoff", lat=1.0),
        ans("q2", sit="calm_play", lat=3.0),
        ans("q3", correct=False, sit="throw_in", lat=3.0),
        ans("q4", cat="player", sit="calm_play", lat=5.0),
    ]
    r = run(a).radar
    assert (r.crystal_ball.score, r.crystal_ball.answered, r.crystal_ball.correct) == (44, 1, 0)
    assert (r.galaxy_brain.score, r.galaxy_brain.correct) == (100, 1)  # 30/22.8 capped
    assert (r.pause_pro.score, r.pause_pro.answered, r.pause_pro.correct) == (44, 1, 0)
    assert (r.bestie_mode.score, r.bestie_mode.correct) == (100, 1)
    assert (r.lightning_thumbs.score, r.lightning_thumbs.answered,
            r.lightning_thumbs.correct) == (76, 4, 2)  # 100 - 12 * (3 - 1)
    assert (r.on_fire.score, r.on_fire.answered, r.on_fire.correct) == (35, 4, 2)  # 20 -> floor


def test_radar_galaxy_and_pause_averages():
    # galaxy: wrong calm_play 10 + correct stalled_near_box 30 -> avg 20 -> 87.7 -> 88
    a = [ans("q1", correct=False), ans("q2", sit="stalled_near_box", open_at=20)]
    g = run(a).radar.galaxy_brain
    assert (g.score, g.answered, g.correct) == (88, 2, 1)
    # pause: correct throw_in with hint 20 -> 88; two wrong goal_kick 10 -> 44
    assert run([ans("q1", sit="throw_in", hint=True)]).radar.pause_pro.score == 88
    b = [ans("q1", correct=False, sit="goal_kick"), ans("q2", correct=False, sit="goal_kick")]
    assert run(b).radar.pause_pro.score == 44


def test_radar_points_axis_reaches_exactly_100():
    flags = [True, True, False, True, False]  # 30+34+10+30+10 = 114 = 5 * 22.8
    assert run(_cards(flags)).radar.galaxy_brain.score == 100


@pytest.mark.parametrize("streak,score", [(0, 35), (1, 35), (2, 40), (3, 60), (4, 80),
                                          (5, 100), (8, 100)])
def test_on_fire_scale(streak, score):
    a = _cards([False] + [True] * streak + [False]) if streak else _cards([False])
    assert run(a).radar.on_fire.score == score


@pytest.mark.parametrize("sit", get_args(MomentSituation))
def test_every_situation_maps_to_its_axis(sit):
    expected = "galaxy_brain" if sit in ("calm_play", "stalled_near_box") else "pause_pro"
    radar = run([ans("q1", sit=sit)]).radar
    for axis in ("crystal_ball", "galaxy_brain", "pause_pro", "bestie_mode"):
        value = getattr(radar, axis)
        assert (value is not None) == (axis == expected), axis


@pytest.mark.parametrize("sit", get_args(MomentSituation))
def test_forecast_and_player_cards_ignore_situation(sit):
    f = run([ans("q1", cat="forecast", sit=sit)]).radar
    p = run([ans("q1", cat="player", sit=sit)]).radar
    assert f.crystal_ball is not None and f.galaxy_brain is None and f.pause_pro is None
    assert p.bestie_mode is not None and p.galaxy_brain is None and p.pause_pro is None


def test_latency_median_even_count():
    a = [ans("q1", lat=1.0), ans("q2", lat=2.0), ans("q3", lat=3.0), ans("q4", lat=5.0)]
    assert run(a).radar.lightning_thumbs.score == 82  # median 2.5 -> 100 - 12 * 1.5


@pytest.mark.parametrize("lat,score", [(0.0, 100), (1.0, 100), (6.0, 40), (30.0, 35)])
def test_latency_boundaries(lat, score):
    assert run([ans("q1", lat=lat)]).radar.lightning_thumbs.score == score


def test_radar_ignores_unanswered_latency():
    a = [ans("q1", lat=1.0), ans("q2", answered=False)]
    assert run(a).radar.lightning_thumbs.answered == 1


# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
#
# Best moment, determinism, contract
#
# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%


def test_best_moment_priority():
    a = [ans(f"q{i}") for i in range(1, 5)]
    assert best_moment(a, oc(a)) == {"kind": "best_streak", "value": 4}
    a = [ans("q1", cat="player"), ans("q2", cat="player", correct=False),
         ans("q3", cat="player"), ans("q4", correct=False)]
    assert best_moment(a, oc(a)) == {"kind": "player_correct", "value": 2}
    a = [ans("q1", cat="forecast"), ans("q2", correct=False)]
    assert best_moment(a, oc(a)) == {"kind": "forecast_correct", "value": 1}
    a = [ans("q1"), ans("q2"), ans("q3", correct=False)]
    assert best_moment(a, oc(a)) == {"kind": "best_streak", "value": 2}
    a = [ans("q1"), ans("q2", correct=False), ans("q3", correct=False)]
    assert best_moment(a, oc(a)) == {"kind": "correct", "value": 1}
    a = [ans("q1", correct=False, answered=True), ans("q2", answered=False)]
    assert best_moment(a, oc(a)) == {"kind": "answered", "value": 1}


def test_determinism():
    a = [ans("q1"), ans("q2", cat="forecast"), ans("q3", cat="player")]
    assert run(a, n_typical=77) == run(list(reversed(a)), n_typical=77)


def test_dump_validates_against_contract():
    a = [ans("q1"), ans("q2", cat="forecast", sit="kickoff"), ans("q3", answered=False)]
    fs = run(a, n_typical=10, terms_learned=["offside"], others_final=[1, 2])
    assert isinstance(fs, FinalScore)
    validate("score", dump(fs))
    validate("score", dump(run([], {})))


def test_dump_with_ranks_and_partial_radar_validates():
    fs = run(_mine(), n_typical=4, others_final=[100, 200], room_final=[100],
             friends_final=[50])
    d = dump(fs)
    assert d["rank"] == {"room": 1, "global": 1, "friends": 1}
    assert d["radar"]["crystal_ball"] is None and d["radar"]["galaxy_brain"]["score"] == 100
    validate("score", d)
