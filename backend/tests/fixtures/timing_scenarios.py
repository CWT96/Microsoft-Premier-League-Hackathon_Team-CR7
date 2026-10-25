"""timing_scenarios.py — hand-built DetectorState sequences for the timing tests (SPEC 4.10).

Last Modified: 2026-10-24 22:40, scenarios A-R as small builders
Inputs:  none
Outputs: lists of DetectorState, one per match second

Workflow:
  - ``st`` builds one state with calm-play defaults; ``run`` builds consecutive seconds
  - ``scenario_*`` functions return the states of one scenario

CAUTION: none
"""

from __future__ import annotations

from typing import Any

from backend.core.types import DetectorState

CALM: dict[str, Any] = dict(
    mode="live", situation="open_play", threat=0.0, movement=0.2, pressure_m=9.0,
    ball_xy=(20.0, 34.0), ball_zone="own_third", possession_team="NBR",
    seconds_since_shot=300.0, attack_active=False, restart_pending=False, restart_taken=False,
)
ATTACK: dict[str, Any] = dict(
    mode="attack", situation="attack", threat=0.6, movement=0.9, pressure_m=2.0,
    ball_xy=(92.0, 30.0), ball_zone="final_third", seconds_since_shot=5.0, attack_active=True,
)
LINGER: dict[str, Any] = dict(  # cost between 0.45 and 0.70 (neither calm nor silent)
    threat=0.15, movement=0.5, pressure_m=5.5, ball_xy=(80.0, 30.0), ball_zone="final_third",
    seconds_since_shot=60.0,
)


def st(t: float, half: int = 1, **kw: Any) -> DetectorState:
    """One DetectorState; calm own-third play unless overridden."""
    base: dict[str, Any] = dict(
        t=t, half=half, break_kind=None, score_home=0, score_away=0, dull_score=0.7,
        last_attack_end_t=None,
    )
    base.update(CALM)
    base.update(kw)
    return DetectorState(**base)


def run(start: float, n: int, half: int = 1, **kw: Any) -> list[DetectorState]:
    """``n`` consecutive seconds starting at ``start`` with the same overrides."""
    return [st(start + i, half, **kw) for i in range(n)]


def scenario_a_calm_feed() -> list[DetectorState]:
    return run(100, 21)


def scenario_b_fast_attack() -> list[DetectorState]:
    return (run(100, 4) + run(104, 5, **ATTACK) + run(109, 3, last_attack_end_t=109.0, **LINGER)
            + run(112, 12, last_attack_end_t=109.0))


def scenario_c_throw_in() -> list[DetectorState]:
    return run(200, 4, situation="throw_in", restart_pending=True, ball_zone="middle_third",
               ball_xy=(50.0, 0.0), movement=0.1)


def scenario_d_after_attack() -> list[DetectorState]:
    return run(300, 3, **ATTACK) + run(303, 12, last_attack_end_t=303.0)


def scenario_e_corner() -> list[DetectorState]:
    wait = run(400, 6, situation="corner", restart_pending=True, ball_zone="final_third",
               ball_xy=(104.0, 0.0), movement=0.1, situation_detail={"restart": "corner",
                                                                    "team": "NBR"})
    live = run(406, 4, situation="corner", restart_pending=False, restart_taken=False,
               ball_zone="final_third", ball_xy=(95.0, 30.0), threat=0.4, movement=0.8)
    return wait + live


def scenario_f_substitution() -> list[DetectorState]:
    return run(500, 3, situation="substitution", movement=0.0)


def scenario_g_half_end() -> list[DetectorState]:
    return run(2690, 10)


def scenario_k_attack_after_open() -> list[DetectorState]:
    return run(600, 2) + run(602, 6, **ATTACK)


def scenario_n_goal() -> list[DetectorState]:
    return (run(700, 5, **ATTACK)
            + run(705, 12, situation="after_goal", score_home=1, last_attack_end_t=705.0))


def scenario_o_player_slot() -> list[DetectorState]:
    return run(2395, 3) + run(2398, 7, **ATTACK) + run(2405, 20, last_attack_end_t=2405.0)


def scenario_p_referee() -> list[DetectorState]:
    return run(800, 3, situation="referee_decision", movement=0.0)


def scenario_q_stalled() -> list[DetectorState]:
    return run(900, 3, situation="stalled_near_box", ball_zone="final_third",
               ball_xy=(85.0, 30.0), movement=0.1, seconds_since_shot=30.0)


def scenario_r_penalty() -> list[DetectorState]:
    return run(1000, 5, situation="penalty", restart_pending=True, ball_zone="final_third",
               ball_xy=(94.0, 34.0), movement=0.0, situation_detail={"restart": "penalty"})


def scenario_water_break() -> list[DetectorState]:
    return (run(1318, 2) + run(1320, 5, mode="break", break_kind="water_break",
                               situation="water_break", movement=0.0) + run(1325, 8))
