"""Detector [1]: sub-scores, attacks, situations, restarts, breaks, tracking signals."""

from __future__ import annotations

import logging

import pytest

from backend.core.types import Match, MatchEvent, Roster, Tracking, TrackingFrame
from backend.detect import detector as det_mod
from backend.detect.detector import Detector
from backend.detect.xt_grid import GRID_COLS, GRID_ROWS, XT_GRID, xt

ROSTER = Roster("NBR", "CAL", "Northbridge", "Calder", ())
_ids = iter(range(1, 100000))


def ev(t, team, typ, start=None, end=None, half=1, outcome=None, pid=None, rel=None):
    return MatchEvent(id=f"e{next(_ids):05d}", t=t, half=half, team=team, type=typ,
                      player_id=pid, start=start, end=end, outcome=outcome,
                      related_player_id=rel)


def new() -> Detector:
    return Detector(ROSTER, "NBR", "CAL")


def drive(d, events, t0, t1, half=1, frames=None):
    """Step seconds t0..t1 inclusive; returns {second: state}."""
    out = {}
    for sec in range(t0, t1 + 1):
        now = [e for e in events if e.half == half and sec <= e.t < sec + 1]
        out[sec] = d.step(half, float(sec), now, frames(sec) if frames else None)
    return out


def passes(team, times, x=40.0, y=34.0, step=0.0):
    return [ev(t, team, "pass", (x + step * i, y), (x + step * i + 3, y), outcome="complete")
            for i, t in enumerate(times)]


def frame(sec, players, ball, half=1):
    return TrackingFrame(float(sec), half, tuple(players), ball)


# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
#
# xT grid
#
# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%


def test_xt_grid_shape_and_rise_towards_goal() -> None:
    assert len(XT_GRID) == GRID_ROWS == 8 and all(len(r) == GRID_COLS == 12 for r in XT_GRID)
    row = [xt(x, 34.0) for x in range(2, 105, 9)]
    assert row == sorted(row) and row[0] < 0.02 and row[-1] > 0.4
    assert xt(20, 34) < 0.05 and xt(95, 34) > 0.15
    assert all(0.0 <= v <= 1.0 for r in XT_GRID for v in r)


def test_xt_clamps_off_pitch() -> None:
    assert xt(-5, -5) == XT_GRID[0][0] and xt(200, 200) == XT_GRID[7][11]


# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
#
# Dull score and sub-scores
#
# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%


def test_dull_parts_back_passing_is_fully_dull() -> None:
    d = new()
    s = drive(d, passes("NBR", [t + 0.5 for t in range(0, 36, 2)]), 0, 36)[36]
    assert s.dull_parts == pytest.approx({
        "pass_rate": 1.0, "not_final_third": 1.0, "low_threat": 1.0, "shot_gap": 36 / 180,
        "possession": 1.0})
    assert s.dull_score == pytest.approx(sum(s.dull_parts.values()) / 5)
    assert s.seconds_since_shot == pytest.approx(36)


def test_pass_rate_ramps_between_4_and_10_per_minute() -> None:
    d = new()
    s = drive(d, passes("NBR", [10.5, 20.5]), 0, 24)[24]  # 2 passes in 25 s = 4.8 per min
    assert s.dull_parts["pass_rate"] == pytest.approx((4.8 - 4) / 6)


def test_not_final_third_share() -> None:
    d = new()
    evs = [ev(5.5, "NBR", "pass", (40, 30), (60, 30)), ev(8.5, "NBR", "pass", (80, 30), (85, 30))]
    s = drive(d, evs, 0, 10)[10]
    assert s.dull_parts["not_final_third"] == pytest.approx(0.5)


def test_not_final_third_uses_acting_team_frame_for_away() -> None:
    d = new()  # CAL event at x=20 in its own frame is deep in CAL's own half, not final third
    s = drive(d, [ev(2.5, "CAL", "pass", (20, 30), (25, 30))], 0, 4)[4]
    assert s.dull_parts["not_final_third"] == 1.0


def test_low_threat_falls_linearly_between_005_and_015() -> None:
    d = new()
    s = drive(d, [ev(2.5, "NBR", "pass", (71, 4), (73, 4))], 0, 4)[4]  # zone value 0.072
    assert s.dull_parts["low_threat"] == pytest.approx(1 - (0.072 - 0.05) / 0.10)
    d2 = new()
    s2 = drive(d2, [ev(2.5, "NBR", "carry", (95, 34), (100, 34))], 0, 4)[4]
    assert s2.dull_parts["low_threat"] == 0.0


def test_shot_gap_resets_on_shot() -> None:
    d = new()
    s = drive(d, [ev(10.5, "NBR", "shot", (90, 34), (105, 34), outcome="off_target")], 0, 100)
    assert s[100].seconds_since_shot == pytest.approx(100 - 10.5)
    assert s[100].dull_parts["shot_gap"] == pytest.approx((100 - 10.5) / 180)


def test_possession_counts_passes_and_resets_on_turnover() -> None:
    d = new()
    evs = passes("NBR", [1.5, 3.5, 5.5]) + [ev(7.5, "CAL", "interception", (60, 30))]
    s = drive(d, evs, 0, 8)
    assert s[6].dull_parts["possession"] == pytest.approx(0.5)
    assert s[8].dull_parts["possession"] == 0.0 and s[8].possession_team == "CAL"


def test_window_forgets_old_events() -> None:
    d = new()
    s = drive(d, passes("NBR", [1.5, 2.5, 3.5, 4.5, 5.5, 6.5]), 0, 40)
    assert s[10].dull_parts["pass_rate"] > 0
    assert s[40].dull_parts["pass_rate"] == 0.0


def test_threat_is_max_xt_of_last_10_seconds() -> None:
    d = new()
    s = drive(d, [ev(5.5, "NBR", "pass", (80, 34), (92, 34))], 0, 20)
    assert s[6].threat == pytest.approx(xt(92, 34))
    assert s[14].threat == pytest.approx(xt(92, 34))
    assert s[16].threat == 0.0


# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
#
# Tracking signals
#
# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%


def _runners(speed):
    """Frames where two players (one per team) move `speed` m/s along x."""
    def f(sec):
        return frame(sec, [("n1", "NBR", 20 + speed * sec, 30),
                           ("c1", "CAL", 60 + speed * sec, 40)], (50.0, 34.0))
    return f


def test_movement_none_without_tracking() -> None:
    s = drive(new(), passes("NBR", [1.5]), 0, 5)[5]
    assert s.movement is None and s.pressure_m is None


def test_movement_normalised_speed() -> None:
    assert drive(new(), [], 0, 6, frames=_runners(0.0))[6].movement == 0.0
    assert drive(new(), [], 0, 6, frames=_runners(1.5))[6].movement == pytest.approx(0.5)
    assert drive(new(), [], 0, 6, frames=_runners(5.0))[6].movement == 1.0
    assert drive(new(), [], 0, 0, frames=_runners(1.5))[0].movement is None  # one frame only


def test_pressure_is_distance_to_nearest_opponent() -> None:
    d = new()
    evs = passes("NBR", [0.5])
    s = drive(d, evs, 0, 1, frames=lambda sec: frame(sec, [
        ("n1", "NBR", 50, 34), ("c1", "CAL", 53, 38), ("c2", "CAL", 60, 34)], (50.0, 34.0)))[1]
    assert s.pressure_m == pytest.approx(5.0)  # nearest CAL player, 3-4-5 triangle


def test_pressure_for_away_possession_uses_home_players() -> None:
    d = new()
    s = drive(d, [ev(0.5, "CAL", "pass", (50, 34), (55, 34))], 0, 1, frames=lambda sec: frame(
        sec, [("n1", "NBR", 52, 34), ("c1", "CAL", 50, 34), ("c2", "CAL", 51, 34)],
        (50.0, 34.0)))[1]
    assert s.pressure_m == pytest.approx(2.0)


def test_ball_xy_converted_to_possession_frame_for_away() -> None:
    f = lambda sec: frame(sec, [], (80.0, 20.0))  # noqa: E731  fixed frame, near CAL's goal
    home = drive(new(), [ev(0.5, "NBR", "pass", (60, 30), (70, 30))], 0, 1, frames=f)[1]
    away = drive(new(), [ev(0.5, "CAL", "pass", (60, 30), (70, 30))], 0, 1, frames=f)[1]
    assert home.ball_xy == (80.0, 20.0) and home.ball_zone == "final_third"
    assert away.ball_xy == (25.0, 48.0) and away.ball_zone == "own_third"


def test_ball_xy_from_events_without_tracking() -> None:
    s = drive(new(), [ev(0.5, "CAL", "pass", (30, 20), (50, 25))], 0, 1)[1]
    assert s.ball_xy == (50, 25) and s.ball_zone == "middle_third"
    assert s.possession_team == "CAL"


# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
#
# Attacks
#
# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%


def test_attack_enters_when_ball_reaches_final_third() -> None:
    d = new()
    s = drive(d, [ev(2.5, "NBR", "pass", (55, 30), (60, 30)),
                  ev(5.5, "NBR", "carry", (60, 30), (75, 30))], 0, 8)
    assert not s[4].attack_active and s[4].mode == "live" and s[4].situation == "open_play"
    assert s[5].attack_active and s[5].mode == "attack" and s[5].situation == "attack"


def test_attack_enters_on_shot_outside_final_third() -> None:
    s = drive(new(), [ev(2.5, "NBR", "shot", (60, 34), (105, 34), outcome="off_target")], 0, 4)
    assert s[2].attack_active


def test_attack_enters_on_high_xt(monkeypatch) -> None:
    monkeypatch.setattr(det_mod, "ATTACK_ENTRY_XT", 0.03)
    s = drive(new(), [ev(2.5, "NBR", "pass", (50, 34), (60, 34))], 0, 4)  # zone value 0.047
    assert s[2].attack_active


def test_foul_and_card_are_dead_balls_not_attack_entries() -> None:
    # ASSUMPTION: a foul or card with the ball outside play is a referee decision (calm per 4.3)
    s = drive(new(), [ev(2.5, "CAL", "foul", (20, 30)), ev(3.5, "CAL", "card", outcome="yellow")],
              0, 4)
    assert not s[3].attack_active and s[3].situation == "referee_decision"


def _enter(d, extra=(), until=12):
    evs = [ev(2.5, "NBR", "carry", (60, 30), (75, 30))] + list(extra)
    return drive(d, evs, 0, until)


@pytest.mark.parametrize("typ,kw", [
    ("shot", {"start": (90, 34), "end": (105, 34), "outcome": "off_target"}),
    ("goal", {"start": (105, 34), "outcome": "goal"}),
    ("save", {"start": (3, 34), "outcome": "saved"}),
    ("foul", {"start": (20, 30)}),
    ("out", {"start": (0, 26)}),
])
def test_attack_exits_on_each_trigger(typ, kw) -> None:
    team = "CAL" if typ in ("save", "foul", "out") else "NBR"
    s = _enter(new(), [ev(6.5, team, typ, **kw)])
    assert s[5].attack_active and not s[6].attack_active and s[6].mode == "live"
    assert s[6].last_attack_end_t is not None and s[6].last_attack_end_t <= 6.5 + 1


def test_attack_exit_sets_last_attack_end_t() -> None:
    s = _enter(new(), [ev(6.5, "NBR", "shot", (90, 34), (105, 34), outcome="off_target")])
    assert s[5].last_attack_end_t is None and s[6].last_attack_end_t == 6.0


def test_attack_exits_after_5s_back_in_middle_third() -> None:
    s = _enter(new(), [ev(6.5, "NBR", "pass", (75, 30), (60, 30))], until=20)
    assert s[10].attack_active and not s[11].attack_active
    assert s[11].last_attack_end_t == 11.0 and s[11].situation == "open_play"


def test_ball_returning_to_final_third_resets_middle_third_timer() -> None:
    evs = [ev(6.5, "NBR", "pass", (75, 30), (60, 30)), ev(9.5, "NBR", "pass", (60, 30), (80, 30))]
    s = _enter(new(), evs, until=18)
    assert s[18].attack_active


def test_stall_without_tracking_after_10s_without_progress() -> None:
    evs = [ev(t + 0.5, "NBR", "pass", (76, 30 + (t % 2) * 4), (76, 34 - (t % 2) * 4))
           for t in range(3, 30, 2)]
    s = _enter(new(), evs, until=30)
    stalled = [k for k, v in s.items() if v.situation == "stalled_near_box"]
    assert stalled and s[stalled[0]].mode == "live" and not s[stalled[0]].attack_active
    assert s[stalled[0]].last_attack_end_t == float(stalled[0])
    assert 11 <= stalled[0] <= 14
    assert s[30].situation == "stalled_near_box"  # ball never left the final third


def test_progress_keeps_attack_alive_without_tracking() -> None:
    evs = [ev(3.5 + 3 * i, "NBR", "carry", (76 + 3 * i, 34), (79 + 3 * i, 34)) for i in range(8)]
    s = _enter(new(), evs, until=27)
    assert s[26].attack_active


def test_stall_with_tracking_needs_low_movement_for_10s() -> None:
    standing = lambda sec: frame(sec, [("n1", "NBR", 70, 30)], (76.0, 30.0))  # noqa: E731
    evs = [ev(2.5, "NBR", "carry", (60, 30), (76, 30))]
    s = drive(new(), evs, 0, 20, frames=standing)
    assert s[10].attack_active and s[20].situation == "stalled_near_box"
    runner = lambda sec: frame(sec, [("n1", "NBR", 40 + 4 * sec, 30)], (76.0, 30.0))  # noqa: E731
    s2 = drive(new(), evs, 0, 20, frames=runner)
    assert s2[20].attack_active


def _slow(speed_mps):
    return lambda sec: frame(sec, [("n1", "NBR", 50 + speed_mps * sec, 30)], (76.0, 30.0))


def test_stall_threshold_is_055_normalised() -> None:
    evs = [ev(2.5, "NBR", "carry", (60, 30), (76, 30))]
    slow = drive(new(), evs, 0, 20, frames=_slow(0.5 * 3.0))  # movement 0.5
    fast = drive(new(), evs, 0, 20, frames=_slow(0.6 * 3.0))  # movement 0.6
    assert slow[20].situation == "stalled_near_box"
    assert fast[20].attack_active


def test_no_reentry_while_stalled_until_ball_leaves_final_third() -> None:
    evs = [ev(2.5, "NBR", "carry", (60, 30), (76, 30))]
    standing = lambda sec: frame(sec, [("n1", "NBR", 70, 30)], (76.0, 30.0))  # noqa: E731
    d = new()
    s = drive(d, evs, 0, 25, frames=standing)
    assert not s[25].attack_active
    at = lambda x: (lambda sec: frame(sec, [("n1", "NBR", 70, 30)], (x, 30.0)))  # noqa: E731
    back = drive(d, [], 26, 26, frames=at(50.0))
    assert back[26].situation == "open_play"
    again = drive(d, [], 27, 27, frames=at(80.0))
    assert again[27].attack_active


# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
#
# Situations, restarts, score, breaks
#
# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%


def test_kickoff_then_open_play() -> None:
    evs = [ev(0.0, "NBR", "kickoff", (52.5, 34)), ev(1.5, "NBR", "pass", (52.5, 34), (50, 30))]
    s = drive(new(), evs, 0, 3)
    assert s[0].situation == "kickoff" and s[0].possession_team == "NBR"
    assert s[1].situation == "open_play"


@pytest.mark.parametrize("typ", ["throw_in", "goal_kick", "corner", "free_kick", "penalty"])
def test_restart_pending_until_taker_acts(typ) -> None:
    evs = [ev(3.5, "NBR", typ, (30, 0)), ev(12.5, "NBR", "pass", (30, 0), (40, 10))]
    s = drive(new(), evs, 0, 14)
    assert not s[2].restart_pending and s[2].situation == "open_play"
    for k in range(3, 12):
        assert s[k].restart_pending and not s[k].restart_taken and s[k].situation == typ
        assert s[k].situation_detail["restart"] == typ and s[k].possession_team == "NBR"
    assert not s[12].restart_pending and s[12].restart_taken
    assert s[12].situation_detail["restart"] == typ
    assert not s[13].restart_taken and s[13].situation_detail == {}


def test_restart_near_box_flag_uses_awarded_team_frame() -> None:
    near = drive(new(), [ev(1.5, "CAL", "corner", (105, 0))], 0, 2)[2].situation_detail
    far = drive(new(), [ev(1.5, "CAL", "free_kick", (60, 30))], 0, 2)[2].situation_detail
    wide = drive(new(), [ev(1.5, "CAL", "throw_in", (80, 2))], 0, 2)[2].situation_detail
    assert near["near_box"] is True and near["team"] == "CAL" and near["x"] == 105
    assert far["near_box"] is False and wide["near_box"] is False


def test_restart_taken_in_same_second_as_award() -> None:
    evs = [ev(3.2, "NBR", "throw_in", (40, 0)), ev(3.8, "NBR", "pass", (40, 0), (45, 9))]
    s = drive(new(), evs, 0, 4)
    assert s[3].restart_taken and not s[3].restart_pending


def test_restart_award_ends_attack_and_blocks_reentry_until_kick() -> None:
    evs = [ev(2.5, "NBR", "carry", (60, 30), (75, 30)), ev(6.5, "NBR", "corner", (105, 0)),
           ev(12.5, "NBR", "pass", (105, 0), (95, 25))]
    s = drive(new(), evs, 0, 14)
    assert not s[7].attack_active and s[7].situation == "corner"
    assert s[12].restart_taken and s[12].attack_active  # ball in final third again at the kick


def test_referee_decision_for_a_few_seconds_or_until_award() -> None:
    evs = [ev(3.5, "CAL", "foul", (20, 30)), ev(5.5, "NBR", "free_kick", (30, 40))]
    s = drive(new(), evs, 0, 20)
    assert s[3].situation == "referee_decision" and s[4].situation == "referee_decision"
    assert s[5].situation == "free_kick"
    only = drive(new(), [ev(3.5, "CAL", "var_check")], 0, 20)
    assert only[5].situation == "referee_decision" and only[15].situation == "open_play"
    card = drive(new(), [ev(3.5, "CAL", "card", outcome="yellow")], 0, 5)
    assert card[4].situation == "referee_decision"


def test_substitution_and_injury_last_until_play_resumes() -> None:
    sub = ev(3.5, "NBR", "substitution", pid="nbr-9", rel="nbr-14")
    s = drive(new(), [sub, ev(40.5, "NBR", "pass", (40, 30), (45, 30))], 0, 41)
    assert s[3].situation == "substitution" and s[30].situation == "substitution"
    assert s[3].situation_detail == {"player_id": "nbr-9", "related_player_id": "nbr-14"}
    assert s[40].situation == "open_play"
    inj = drive(new(), [ev(3.5, "CAL", "injury", pid="cal-4"), ev(9.5, "CAL", "pass", (30, 30),
                                                                 (40, 30))], 0, 10)
    assert inj[5].situation == "injury" and inj[5].situation_detail == {"player_id": "cal-4"}
    assert inj[9].situation == "open_play"


def test_goal_sets_score_after_goal_and_last_attack_end() -> None:
    evs = [ev(2.5, "NBR", "carry", (60, 30), (80, 30)),
           ev(5.5, "NBR", "shot", (90, 34), (105, 34), outcome="goal"),
           ev(5.8, "NBR", "goal", (105, 34), outcome="goal"),
           ev(40.5, "CAL", "kickoff", (52.5, 34)), ev(42.5, "CAL", "pass", (52.5, 34), (50, 30))]
    s = drive(new(), evs, 0, 44)
    assert (s[5].score_home, s[5].score_away) == (1, 0)
    assert s[5].situation == "after_goal" and not s[5].attack_active
    assert s[5].last_attack_end_t == pytest.approx(5.8)
    assert s[30].situation == "after_goal" and s[39].situation == "after_goal"
    assert s[40].situation == "kickoff" and s[40].possession_team == "CAL"
    assert s[44].situation == "open_play" and s[44].ball_xy == (50, 30)


def test_away_goal_counts_for_away() -> None:
    s = drive(new(), [ev(1.5, "CAL", "goal", (105, 34))], 0, 2)[2]
    assert (s.score_home, s.score_away) == (0, 1)


def test_goal_without_attack_still_sets_last_attack_end_t() -> None:
    s = drive(new(), [ev(1.5, "NBR", "goal", (105, 34))], 0, 2)[2]
    assert s.last_attack_end_t == 1.5


def test_water_break_mode_for_its_duration() -> None:
    evs = [ev(10.5, "NBR", "water_break_start"), ev(100.5, "NBR", "water_break_end"),
           ev(105.5, "NBR", "pass", (40, 30), (45, 30))]
    s = drive(new(), evs, 0, 106)
    assert s[9].mode == "live" and s[9].break_kind is None
    assert s[10].mode == "break" and s[10].break_kind == "water_break"
    assert s[10].situation == "water_break" and s[99].situation == "water_break"
    assert s[100].mode == "live" and s[100].break_kind is None


def test_half_time_and_second_half_continuity() -> None:
    d = new()
    evs = [ev(10.5, "NBR", "shot", (90, 34), (105, 34), outcome="off_target"),
           ev(2790.2, "NBR", "half_end")]
    d.step(1, 10.0, [evs[0]])
    ht = drive(d, evs, 2788, 2790)[2790]
    assert ht.mode == "break" and ht.break_kind == "half_time" and ht.situation == "half_time"
    s2 = d.step(2, 2700.0, [ev(2700.0, "CAL", "kickoff", (52.5, 34), half=2)])
    assert s2.mode == "live" and s2.situation == "kickoff" and s2.half == 2
    # half 1 ran to 2791 s, so half 2 starts at continuous 2791 s
    assert s2.seconds_since_shot == pytest.approx((2700 + 91) - 10.5)
    assert s2.dull_parts["possession"] == 0.0


def test_full_time() -> None:
    s = drive(new(), [ev(5.5, "NBR", "full_end", half=1)], 0, 6)
    assert s[5].mode == "full_time" and s[5].situation == "full_time"


def test_new_events_are_this_seconds_events() -> None:
    e1 = ev(1.2, "NBR", "pass", (40, 30), (45, 30))
    s = drive(new(), [e1], 0, 2)
    assert s[0].new_events == () and s[1].new_events == (e1,) and s[2].new_events == ()


def test_deterministic() -> None:
    evs = passes("NBR", [1.5, 4.5, 8.5], x=60, step=8) + [ev(12.5, "CAL", "foul", (20, 20))]
    a = [(k, v) for k, v in drive(new(), evs, 0, 30).items()]
    b = [(k, v) for k, v in drive(new(), evs, 0, 30).items()]
    assert a == b


# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
#
# run(match)
#
# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%


def _tiny_match(with_tracking: bool) -> Match:
    evs = (ev(0.0, "NBR", "kickoff", (52.5, 34)), ev(3.5, "NBR", "pass", (50, 30), (60, 30)),
           ev(20.0, "NBR", "half_end"), ev(2700.0, "CAL", "kickoff", (52.5, 34), half=2),
           ev(2710.0, "CAL", "full_end", half=2))
    tracking = None
    if with_tracking:
        frames = {(h, s): frame(s, [("n1", "NBR", 50, 30), ("c1", "CAL", 55, 30)], (50.0, 30.0), h)
                  for h, rng in ((1, range(0, 21)), (2, range(2700, 2711))) for s in rng}
        tracking = Tracking(frames)
    return Match("m", ROSTER, evs, tracking, first_half_end_t=20.0, full_end_t=2710.0)


def test_run_covers_lobby_both_halves_and_ends_in_full_time() -> None:
    states = list(Detector(ROSTER, "NBR", "CAL").run(_tiny_match(False)))
    assert states[0].mode == "lobby"
    assert [s.t for s in states[1:22]] == [float(i) for i in range(21)]
    assert states[21].mode == "break" and states[21].break_kind == "half_time"
    assert states[22].half == 2 and states[22].t == 2700.0
    assert states[-1].mode == "full_time" and states[-1].t == 2710.0
    assert len(states) == 1 + 21 + 11
    assert all(s.movement is None for s in states)


def test_run_uses_tracking_when_present() -> None:
    states = list(Detector(ROSTER, "NBR", "CAL").run(_tiny_match(True)))
    assert states[5].movement == 0.0 and states[5].pressure_m == pytest.approx(5.0)


# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
#
# Review fixes: stall latch, after_goal, water timeout, bad data, tackle, half reset
#
# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%


def _stalled_detector():
    """Detector stalled near the box at second 20, standing players, ball at x=76."""
    d = new()
    standing = lambda sec: frame(sec, [("n1", "NBR", 70, 30)], (76.0, 30.0))  # noqa: E731
    s = drive(d, [ev(2.5, "NBR", "carry", (60, 30), (76, 30))], 0, 20, frames=standing)
    assert s[20].situation == "stalled_near_box"
    return d


def test_fast_movement_lifts_stall_latch_at_once() -> None:
    d = _stalled_detector()
    sprint = lambda sec: frame(sec, [("n1", "NBR", 70 + 3 * (sec - 20), 30)],  # noqa: E731
                               (80.0, 30.0))
    s = drive(d, [ev(21.5, "NBR", "pass", (76, 30), (88, 30))], 21, 23, frames=sprint)
    assert s[21].attack_active or s[22].attack_active


def test_through_ball_after_stall_re_enters_attack_within_a_second() -> None:
    d = _stalled_detector()
    # one 7.2 m/s interval inside the 3 s window = 0.8 normalised movement
    sprint = lambda sec: frame(sec, [("n1", "NBR", 70 + 7.2 * (sec - 20), 30)],  # noqa: E731
                               (76.0, 30.0))
    s = drive(d, [], 21, 22, frames=sprint)
    assert s[21].movement == pytest.approx(0.8) and s[21].attack_active


def test_high_xt_event_or_turnover_lifts_stall_latch() -> None:
    d = _stalled_detector()
    standing = lambda sec: frame(sec, [("n1", "NBR", 70, 30)], (76.0, 30.0))  # noqa: E731
    s = drive(d, [ev(21.5, "NBR", "pass", (76, 30), (98, 34))], 21, 21, frames=standing)
    assert s[21].attack_active
    d2 = _stalled_detector()
    s2 = drive(d2, [ev(21.5, "CAL", "interception", (30, 34))], 21, 21, frames=standing)
    assert s2[21].situation != "stalled_near_box"


def test_forward_progress_lifts_stall_latch_without_tracking() -> None:
    evs = [ev(t + 0.5, "NBR", "pass", (76, 30 + (t % 2) * 4), (76, 34 - (t % 2) * 4))
           for t in range(3, 30, 2)]
    d = new()
    s = _enter(d, evs, until=30)
    assert s[30].situation == "stalled_near_box"
    out = drive(d, [ev(31.5, "NBR", "carry", (76, 34), (80, 34))], 31, 32)
    assert out[31].attack_active


def test_after_goal_clears_on_first_on_ball_event_without_kickoff() -> None:
    evs = [ev(2.5, "NBR", "goal", (105, 34)), ev(20.5, "CAL", "pass", (52, 34), (50, 30))]
    s = drive(new(), evs, 0, 21)
    assert s[10].situation == "after_goal" and s[20].situation == "open_play"


def test_water_break_times_out_on_next_on_ball_event(caplog) -> None:
    evs = [ev(5.5, "NBR", "water_break_start"), ev(200.5, "NBR", "pass", (40, 30), (45, 30))]
    with caplog.at_level(logging.WARNING):
        s = drive(new(), evs, 0, 201)
    assert s[199].mode == "break" and s[200].mode == "live"
    assert "no end event" in caplog.text
    early = drive(new(), [ev(5.5, "NBR", "water_break_start"),
                          ev(50.5, "NBR", "pass", (40, 30), (45, 30))], 0, 51)
    assert early[51].mode == "break"


def test_non_finite_tracking_is_ignored() -> None:
    nan = float("nan")
    bad = lambda sec: frame(sec, [("n1", "NBR", nan, 30), ("c1", "CAL", 53, 34)],  # noqa: E731
                            (nan, nan))
    s = drive(new(), [ev(0.5, "NBR", "pass", (40, 30), (45, 30))], 0, 3, frames=bad)[3]
    assert s.ball_xy == (45, 30) and s.pressure_m is None  # fell back to the event position
    assert s.movement == 0.0  # only the finite player counts, and it stands still
    allnan = lambda sec: frame(sec, [("n1", "NBR", nan, nan)], (76.0, 30.0))  # noqa: E731
    d = new()
    t = drive(d, [ev(2.5, "NBR", "carry", (60, 30), (76, 30))], 0, 25, frames=allnan)
    assert all(v.movement is None for v in t.values())  # never NaN


def test_on_ball_event_ends_referee_window() -> None:
    evs = [ev(3.5, "CAL", "foul", (20, 30)), ev(4.5, "NBR", "carry", (70, 30), (78, 30))]
    s = drive(new(), evs, 0, 6)
    assert s[3].situation == "referee_decision" and s[5].attack_active


def test_lost_tackle_does_not_give_possession() -> None:
    evs = [ev(1.5, "NBR", "pass", (40, 30), (45, 30)),
           ev(3.5, "CAL", "tackle", (45, 30), outcome="lost"),
           ev(5.5, "CAL", "tackle", (45, 30), outcome="won")]
    s = drive(new(), evs, 0, 6)
    assert s[4].possession_team == "NBR" and s[6].possession_team == "CAL"


def test_possession_and_ball_reset_at_new_half() -> None:
    d = new()
    d.step(1, 10.0, [ev(10.5, "NBR", "pass", (40, 30), (45, 30))])
    s = d.step(2, 2700.0)
    assert s.possession_team is None and s.ball_xy is None
