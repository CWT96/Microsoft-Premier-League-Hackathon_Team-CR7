"""test_timing.py — timing agent [2b]: cost, gate, policies and scenarios A-R.

Last Modified: 2026-10-24 22:45, first version
Inputs:  backend/tests/fixtures/timing_scenarios.py
Outputs: none

Workflow:
  - drive TimingAgent with hand-built states and assert moments and decisions

CAUTION: none
"""

from __future__ import annotations

import copy
import json

from backend.agents import timing_rules as R
from backend.agents.timing import (
    LLMPolicy, RulePolicy, TimingAgent, interruption_cost, moment_situation,
)
from backend.core.types import DetectorState, Moment, TimingDecision
from backend.tests.contract_utils import validate
from backend.tests.fixtures import timing_scenarios as S

MATCH = "m1"


def drive(states, policy=None):
    agent = TimingAgent(policy or RulePolicy(), MATCH)
    moments, decisions = [], []
    for s in states:
        out = agent.step(s)
        moments += out.moments
        decisions += out.decisions
    return moments, decisions


def acts(decisions, action):
    return [d for d in decisions if d.action == action]


# .............................................................................
# interruption cost
# .............................................................................


def test_cost_calm_is_low_and_attack_is_silent():
    assert interruption_cost(S.st(0))[0] < 0.2
    assert interruption_cost(S.st(0, **S.ATTACK))[0] == R.COST_SILENT


def test_cost_formula_final_third_high_threat_is_silent_without_attack_flag():
    s = S.st(0, threat=0.4, movement=0.9, ball_zone="final_third", pressure_m=2.0,
             seconds_since_shot=5.0)
    assert interruption_cost(s)[0] >= R.SILENT_COST


def test_cost_renormalises_without_tracking():
    with_tr = S.st(0, movement=0.0, pressure_m=9.0)
    without = S.st(0, movement=None, pressure_m=None)
    c_with, sig_with = interruption_cost(with_tr)
    c_without, sig = interruption_cost(without)
    assert sig["movement"] is None and sig["pressure"] is None
    assert 0.0 <= c_without <= 1.0
    assert c_without >= c_with  # same non-zero signals over a smaller weight total


def test_cost_overrides():
    assert interruption_cost(S.st(0, situation="substitution"))[0] == 0.0
    assert interruption_cost(S.st(0, situation="referee_decision"))[0] == R.COST_REFEREE
    assert interruption_cost(S.st(0, situation="stalled_near_box"))[0] == R.COST_STALLED_NEAR_BOX
    assert interruption_cost(S.st(0, situation="throw_in"))[0] == R.COST_STOPPAGE_RESTART
    pend = S.st(0, situation="corner", restart_pending=True)
    assert interruption_cost(pend)[0] == R.COST_SET_PIECE_WAIT
    live = S.st(0, situation="corner", restart_pending=False)
    assert interruption_cost(live)[0] >= R.SILENT_COST


def test_cost_after_goal_silent_then_low():
    early = S.st(710, situation="after_goal", last_attack_end_t=705.0)
    late = S.st(713, situation="after_goal", last_attack_end_t=705.0)
    assert interruption_cost(early)[0] == R.COST_SILENT
    assert interruption_cost(late)[0] == R.COST_AFTER_GOAL


def test_moment_situation_mapping():
    far_fk = S.st(0, situation="free_kick", restart_pending=True, ball_xy=(30.0, 30.0),
                  ball_zone="own_third")
    near_fk = S.st(0, situation="free_kick", restart_pending=True, ball_xy=(88.0, 30.0),
                   ball_zone="final_third")
    assert moment_situation(far_fk) == "referee_decision"
    assert moment_situation(near_fk) == "set_piece_wait"
    assert moment_situation(S.st(0, situation="goal_kick")) == "goal_kick"
    assert moment_situation(S.st(0, **S.ATTACK)) is None
    assert moment_situation(S.st(0, situation="open_play")) == "calm_play"
    assert moment_situation(S.st(0, situation="kickoff")) == "calm_play"


# .............................................................................
# scenarios
# .............................................................................


def test_a_calm_feed_every_four_seconds():
    moments, decisions = drive(S.scenario_a_calm_feed())
    assert [m.open_at for m in moments] == [100, 104, 108, 112, 116, 120]
    assert all(m.situation == "calm_play" and m.kind == "instant" for m in moments)
    assert all(m.lock_at == m.open_at + R.CARD_WINDOW_S and m.lock_rule == "fixed"
               for m in moments)
    assert [m.moment_id for m in moments] == [f"q-{i}" for i in range(1, 7)]
    assert len(acts(decisions, "calm")) == 1
    assert len(acts(decisions, "open")) == 6


def test_b_fast_attack_silent_with_hysteresis():
    moments, decisions = drive(S.scenario_b_fast_attack())
    silent = acts(decisions, "silent")
    assert [d.t for d in silent] == [104]
    assert [m.open_at for m in moments if 104 <= m.open_at < 120] == [117]
    assert moments[0].open_at == 100
    calm = acts(decisions, "calm")
    assert [d.t for d in calm] == [100, 112]  # linger seconds 109-111 stay silent
    blocked = acts(decisions, "blocked")
    assert [(d.t, d.blocked_by) for d in blocked] == [(112, "after_attack")]


def test_c_throw_in():
    moments, decisions = drive(S.scenario_c_throw_in())
    assert moments[0].situation == "throw_in" and moments[0].open_at == 200
    assert moments[0].cost == R.COST_STOPPAGE_RESTART
    assert "Throw-in" in acts(decisions, "open")[0].reason


def test_d_next_moment_eight_seconds_after_attack():
    moments, _ = drive(S.scenario_d_after_attack())
    assert [m.open_at for m in moments] == [311]


def test_e_corner_wait_then_silent_when_live():
    moments, decisions = drive(S.scenario_e_corner())
    assert len(moments) == 1  # one moment per set-piece wait
    m = moments[0]
    assert m.situation == "set_piece_wait" and m.lock_rule == "on_restart"
    assert m.open_at == 400 and m.situation_detail == {"restart": "corner", "team": "NBR"}
    assert [d.t for d in acts(decisions, "silent")] == [406]


def test_f_substitution():
    moments, _ = drive(S.scenario_f_substitution())
    assert moments[0].situation == "substitution" and moments[0].cost == 0.0
    assert moments[0].open_at == 500


def test_g_half_end_guard_blocks_once():
    moments, decisions = drive(S.scenario_g_half_end())
    assert moments == []
    blocked = acts(decisions, "blocked")
    assert len(blocked) == 1 and blocked[0].blocked_by == "half_end"
    assert blocked[0].t == 2690


def test_g_second_half_guard_uses_5400():
    moments, decisions = drive(S.run(5380, 10, half=2))
    assert [m.open_at for m in moments] == [5380, 5384]  # 5385 starts the 15 s guard
    assert [(d.t, d.blocked_by) for d in acts(decisions, "blocked")] == [(5385, "half_end")]


def test_h_kickoff_and_half_time_stacks():
    agent = TimingAgent(RulePolicy(), MATCH)
    lobby = S.st(0, mode="lobby", situation="kickoff")
    out = agent.step(lobby)
    assert [m.kind for m in out.moments] == ["kickoff_stack"] * 3
    assert [m.category for m in out.moments] == ["forecast", "forecast", "player"]
    assert all(m.situation == "kickoff" and m.half == 1 for m in out.moments)
    assert all((m.open_at, m.lock_at) == (0, 60) for m in out.moments)
    assert out.decisions[0].action == "scheduled" and len(out.decisions[0].moment_ids) == 3
    assert agent.step(lobby).moments == ()  # once only
    assert agent.step(S.st(0, situation="kickoff")).moments == ()  # kickoff at t=0: no repeat
    ht = S.st(2760, mode="break", break_kind="half_time", situation="half_time")
    out = agent.step(ht)
    assert [m.kind for m in out.moments] == ["ht_stack"] * 3
    assert [m.category for m in out.moments] == ["forecast", "forecast", "player"]
    assert all(m.half == 2 and m.situation == "half_time" for m in out.moments)
    assert all((m.open_at, m.lock_at) == (2760, 2820) for m in out.moments)
    assert agent.step(ht).moments == ()


def test_h_kickoff_stack_at_t0_live_when_no_lobby():
    agent = TimingAgent(RulePolicy(), MATCH)
    out = agent.step(S.st(0, situation="kickoff"))
    assert [m.kind for m in out.moments] == ["kickoff_stack"] * 3


def test_h_no_stack_when_joining_mid_match():
    moments, _ = drive(S.run(500, 3))
    assert all(m.kind == "instant" for m in moments)


def test_i_llm_junk_falls_back_to_rule_same_moment():
    base_moments, _ = drive(S.scenario_c_throw_in(), RulePolicy())
    junk = [None, "text", {"action": "dance"}, {"action": "open", "tone": "Go 4 it"},
            {"action": "open", "tone": "x" * 41}, {"action": "open"}]
    for bad in junk:
        moments, decisions = drive(S.scenario_c_throw_in(), LLMPolicy(lambda *a, _b=bad, **k: _b))
        assert moments[0] == base_moments[0]
        assert acts(decisions, "open")[0].source == "rule_fallback"


def test_i_llm_exception_and_timeout_fall_back():
    def boom(*a, **k):
        raise TimeoutError("slow")
    moments, decisions = drive(S.scenario_c_throw_in(), LLMPolicy(boom))
    assert moments[0].situation == "throw_in"
    assert acts(decisions, "open")[0].source == "rule_fallback"


def test_i_llm_valid_answer_is_used_and_called_with_contract():
    calls = []

    def fake(agent, prompt, match_id, qid, schema, timeout_s):
        calls.append((agent, json.loads(prompt), match_id, qid, timeout_s))
        return {"action": "open", "tone": "Ball is out!", "reason": "Ball out, players resetting."}
    moments, decisions = drive(S.scenario_c_throw_in(), LLMPolicy(fake))
    assert moments[0].tone == "Ball is out!"
    d = acts(decisions, "open")[0]
    assert d.source == "llm" and d.reason == "Ball out, players resetting."
    agent, payload, match_id, qid, timeout = calls[0]
    assert (agent, match_id, qid, timeout) == ("timing", MATCH, "q-1", R.POLICY_TIMEOUT_S)
    for key in ("t", "half", "score", "cost", "situation", "ball_zone", "possession_team",
                "calm_for_s", "last_situations"):
        assert key in payload


def test_i_llm_hold_delays_one_second_max_two():
    def hold(*a, **k):
        return {"action": "hold", "tone": "Wait", "reason": "Restart looks dangerous."}
    moments, decisions = drive(S.scenario_c_throw_in(), LLMPolicy(hold))
    assert moments[0].open_at == 202  # held at 200 and 201, forced open at 202
    assert [d.t for d in acts(decisions, "hold")] == [200, 201]


def test_j_no_tracking_still_works():
    states = [S.st(100 + i, movement=None, pressure_m=None) for i in range(10)]
    moments, decisions = drive(states)
    assert [m.open_at for m in moments] == [100, 104, 108]
    assert decisions[0].inputs["movement"] is None and decisions[0].inputs["pressure"] is None


def test_k_attack_after_open_no_new_moment():
    moments, _ = drive(S.scenario_k_attack_after_open())
    assert [m.open_at for m in moments] == [600]


def test_l_determinism():
    for build in (S.scenario_a_calm_feed, S.scenario_b_fast_attack, S.scenario_e_corner):
        m1, d1 = drive(build())
        m2, d2 = drive(build())
        assert m1 == m2
        assert [d.to_contract() for d in d1] == [d.to_contract() for d in d2]


def test_m_water_break():
    moments, decisions = drive(S.scenario_water_break())
    wb = [m for m in moments if m.kind == "water_break"]
    assert len(wb) == 1 and wb[0].category == "forecast" and wb[0].situation == "water_break"
    assert wb[0].lock_at == wb[0].open_at + R.WATER_BREAK_WINDOW_S
    assert not [m for m in moments if 1320 < m.open_at < 1325]
    assert acts(decisions, "scheduled")[0].moment_ids == (wb[0].moment_id,)


def test_n_goal_silent_then_after_goal():
    moments, decisions = drive(S.scenario_n_goal())
    assert [m.open_at for m in moments] == [713]
    assert moments[0].situation == "after_goal"
    assert [d.t for d in acts(decisions, "silent")] == [700]


def test_o_player_slot_waits_for_calm():
    moments, _ = drive(S.scenario_o_player_slot())
    player = [m for m in moments if m.kind == "player_instant"]
    assert len(player) == 1
    assert player[0].category == "player" and player[0].open_at == 2413
    assert player[0].situation == "calm_play"
    assert [m.open_at for m in moments if m.open_at > 2413] == [2417, 2421]


def test_o_second_player_slot_in_second_half():
    moments, _ = drive(S.run(4195, 10, half=2))
    # spacing still applies: the feed opened at 4195 and 4199, so the slot fires at 4203
    assert [(m.kind, m.open_at) for m in moments if m.kind == "player_instant"] == [
        ("player_instant", 4203)]


def test_p_referee_decision():
    moments, _ = drive(S.scenario_p_referee())
    assert moments[0].situation == "referee_decision" and moments[0].open_at == 800


def test_q_stalled_near_box():
    moments, _ = drive(S.scenario_q_stalled())
    assert moments[0].situation == "stalled_near_box"
    assert moments[0].cost == R.COST_STALLED_NEAR_BOX


def test_r_penalty():
    moments, _ = drive(S.scenario_r_penalty())
    assert len(moments) == 1
    assert moments[0].situation == "set_piece_wait" and moments[0].lock_rule == "on_restart"


# .............................................................................
# contract, policy and API details
# .............................................................................


def test_every_decision_validates_against_contract():
    builders = [v for k, v in vars(S).items() if k.startswith("scenario_")]
    n = 0
    for build in builders:
        _, decisions = drive(build())
        for d in decisions:
            validate("timing_decision", d.to_contract())
            assert len(d.reason) <= 200 and "cost" in d.inputs
            n += 1
    assert n > 20


def test_rule_policy_tone_is_stable_and_digit_free():
    p = RulePolicy()
    payload = {"moment_situation": "calm_play", "situation": "open_play", "ball_zone": None,
               "signals": {"movement": 0.2}}
    a = p.decide(payload, "q-7", MATCH)
    b = p.decide(payload, "q-7", MATCH)
    assert a == b and a.action == "open" and a.source == "rule"
    for tones in R.TONES.values():
        for tone in tones:
            assert not any(ch.isdigit() for ch in tone) and len(tone) <= R.TONE_MAX_CHARS


def test_peek_next_stop_finds_calm_spell_without_mutating():
    agent = TimingAgent(RulePolicy(), MATCH)
    for s in S.run(100, 3, **S.ATTACK):
        agent.step(s)
    before = copy.deepcopy(agent)
    stop = agent.peek_next_stop(S.run(103, 5, last_attack_end_t=103.0))
    assert stop is not None and stop.reason == "calm_spell" and stop.t == 103
    state = {k: v for k, v in vars(agent).items() if k != "_policy"}
    assert state == {k: v for k, v in vars(before).items() if k != "_policy"}  # ran on a copy
    assert agent.step(S.st(103, last_attack_end_t=103.0)).decisions[0].action == "calm"


def test_peek_next_stop_finds_scheduled_moment():
    agent = TimingAgent(RulePolicy(), MATCH)
    for s in S.run(1300, 18):
        agent.step(s)
    stop = agent.peek_next_stop(S.run(1318, 2) + S.run(
        1320, 3, mode="break", break_kind="water_break", situation="water_break"))
    assert stop is not None and stop.reason == "scheduled" and stop.t == 1320
    assert stop.moment is not None and stop.moment.kind == "water_break"
    assert agent.peek_next_stop(S.run(1318, 5)) is None


def test_types_are_core_types():
    moments, decisions = drive(S.run(100, 2))
    assert isinstance(moments[0], Moment) and isinstance(decisions[0], TimingDecision)
    assert isinstance(S.st(0), DetectorState)


# .............................................................................
# review fixes
# .............................................................................


def test_goal_silence_does_not_depend_on_detector_attack_end():
    states = S.run(705, 12, situation="after_goal", score_home=1)  # last_attack_end_t None
    moments, decisions = drive(states)
    assert [(m.open_at, m.situation) for m in moments] == [(713, "after_goal")]
    assert [d.t for d in acts(decisions, "silent")] == [705]


def test_goal_event_starts_silence_even_in_open_play():
    from backend.core.types import MatchEvent
    goal = MatchEvent(id="e1", t=500, half=1, team="NBR", type="goal")
    states = [S.st(500, new_events=(goal,))] + S.run(501, 14)
    moments, _ = drive(states)
    assert [m.open_at for m in moments] == [508, 512]  # gate holds 8 s after the goal


def test_llm_exception_is_logged_and_reason_recorded(caplog):
    def boom(*a, **k):
        raise TimeoutError("slow")
    with caplog.at_level("WARNING"):
        _, decisions = drive(S.scenario_c_throw_in(), LLMPolicy(boom))
    d = acts(decisions, "open")[0]
    assert d.source == "rule_fallback" and "TimeoutError" in d.inputs["fallback_reason"]
    assert any("timing LLM call failed" in r.getMessage() for r in caplog.records)
    validate("timing_decision", d.to_contract())


def test_llm_rejection_reason_is_recorded():
    _, decisions = drive(S.scenario_c_throw_in(), LLMPolicy(lambda *a, **k: {"action": "skip"}))
    assert acts(decisions, "open")[0].inputs["fallback_reason"] == "action is not open or hold"


def test_llm_answers_other_than_open_or_hold_fall_back():
    for bad in ("silent", "skip", "OPEN", 1, None):
        res = LLMPolicy(lambda *a, _b=bad, **k: {"action": _b, "reason": "x"}).decide(
            {"moment_situation": "throw_in", "signals": {}}, "q-1", MATCH)
        assert res.source == "rule_fallback" and res.action == "open"


def test_llm_open_never_overrides_a_silent_gate():
    calls = []

    def always_open(*a, **k):
        calls.append(a)
        return {"action": "open", "tone": "Go", "reason": "Open anyway."}
    attack = S.run(100, 6, **S.ATTACK)
    moments, _ = drive(attack, LLMPolicy(always_open))
    assert moments == [] and calls == []
    live_set_piece = S.run(200, 4, situation="corner", restart_pending=False)
    assert drive(live_set_piece, LLMPolicy(always_open))[0] == []


def test_non_monotonic_live_step_is_ignored(caplog):
    agent = TimingAgent(RulePolicy(), MATCH)
    assert agent.step(S.st(100)).moments
    with caplog.at_level("WARNING"):
        for bad in (S.st(100), S.st(90), S.st(100, half=1)):
            out = agent.step(bad)
            assert out.moments == () and out.decisions == ()
    assert any("not after" in r.getMessage() for r in caplog.records)
    assert agent.step(S.st(104)).moments  # stream continues normally


def test_future_dated_attack_end_is_ignored_not_blocking():
    states = S.run(2700, 6, half=2, last_attack_end_t=2760.0)  # stale value from half 1
    moments, _ = drive(states)
    assert [m.open_at for m in moments] == [2700, 2704]


def test_movement_zero_counts_as_walking():
    moments, decisions = drive(S.run(200, 3, situation="throw_in", restart_pending=True,
                                     movement=0.0))
    assert "walking back" in acts(decisions, "open")[0].reason


def test_nan_movement_is_treated_as_missing(caplog):
    s = S.st(100, movement=float("nan"))
    cost, sig = interruption_cost(s)
    assert sig["movement"] is None
    assert cost == interruption_cost(S.st(100, movement=None))[0]


def test_forced_open_after_holds_uses_rule_text():
    def hold(*a, **k):
        return {"action": "hold", "tone": "Wait", "reason": "Restart looks dangerous."}
    moments, decisions = drive(S.scenario_c_throw_in(), LLMPolicy(hold))
    d = acts(decisions, "open")[0]
    assert d.inputs["holds_exhausted"] is True and d.reason.endswith("(after 2 holds)")
    assert moments[0].tone in R.TONES["throw_in"] and d.source == "rule"
    validate("timing_decision", d.to_contract())


def test_peek_does_not_call_the_llm_and_does_not_mutate():
    calls = []

    def fake(*a, **k):
        calls.append(a)
        return {"action": "open", "tone": None, "reason": "ok"}
    agent = TimingAgent(LLMPolicy(fake), MATCH)
    agent.peek_next_stop(S.run(100, 10) + S.scenario_water_break())
    assert calls == []
    assert agent._next_id == 1


def test_unknown_detector_value_is_logged_once(caplog):
    agent = TimingAgent(RulePolicy(), MATCH)
    with caplog.at_level("WARNING"):
        for i in range(3):
            agent.step(S.st(100 + i, ball_zone="penalty_area"))
    msgs = [r for r in caplog.records if "unknown ball_zone" in r.getMessage()]
    assert len(msgs) == 1


def test_hysteresis_boundaries(monkeypatch):
    linger = S.st(0, **S.LINGER)
    c = interruption_cost(linger)[0]
    assert R.CANDIDATE_COST < c < R.SILENT_COST
    # exactly at the candidate threshold is calm
    monkeypatch.setattr(R, "CANDIDATE_COST", c)
    _, decisions = drive([S.st(100 + i, **S.LINGER) for i in range(3)])
    assert [d.action for d in decisions][:2] == ["calm", "open"]
    monkeypatch.undo()
    # exactly at the silent threshold is silent
    monkeypatch.setattr(R, "SILENT_COST", c)
    _, decisions = drive([S.st(100 + i, **S.LINGER) for i in range(3)])
    assert [d.action for d in decisions] == ["silent"]
    monkeypatch.undo()
    # in between: calm stays calm-less, silent stays silent
    _, d1 = drive(S.run(100, 2) + [S.st(102 + i, **S.LINGER) for i in range(3)])
    assert [d.action for d in d1] == ["calm", "open"]
    m2, d2 = drive(S.run(100, 2, **S.ATTACK) + [S.st(102 + i, **S.LINGER) for i in range(3)])
    assert [d.action for d in d2] == ["silent"] and m2 == []


def test_m_water_break_full_moment_list():
    moments, _ = drive(S.scenario_water_break())
    assert [(m.kind, m.open_at, m.half) for m in moments] == [
        ("instant", 1318, 1), ("water_break", 1320, 1), ("instant", 1325, 1),
        ("instant", 1329, 1)]  # nothing opens during the break (1321-1324)


def test_p_q_r_full_details():
    p, _ = drive(S.scenario_p_referee())
    assert [(m.open_at, m.lock_at, m.lock_rule, m.situation) for m in p] == [
        (800, 800 + R.CARD_WINDOW_S, "fixed", "referee_decision")]
    q, _ = drive(S.scenario_q_stalled())
    assert [(m.open_at, m.lock_at, m.lock_rule, m.situation) for m in q] == [
        (900, 900 + R.CARD_WINDOW_S, "fixed", "stalled_near_box")]
    r, _ = drive(S.scenario_r_penalty())
    assert [(m.open_at, m.lock_at, m.lock_rule, m.situation, m.situation_detail) for m in r] == [
        (1000, 1000 + R.CARD_WINDOW_S, "on_restart", "set_piece_wait", {"restart": "penalty"})]


def test_q_negative_cases_do_not_make_stalled_moments():
    recent_shot = S.run(900, 3, situation="open_play", ball_zone="final_third",
                        ball_xy=(85.0, 30.0), movement=0.1, seconds_since_shot=5.0)
    busy = S.run(900, 3, situation="open_play", ball_zone="final_third", ball_xy=(85.0, 30.0),
                 movement=0.95, threat=0.3, pressure_m=2.0, seconds_since_shot=30.0)
    for states in (recent_shot, busy):
        assert not [m for m in drive(states)[0] if m.situation == "stalled_near_box"]
    assert drive(busy)[0] == []


def test_e_no_second_set_piece_after_ball_goes_live():
    moments, _ = drive(S.scenario_e_corner() + S.run(410, 12))
    assert [m.situation for m in moments].count("set_piece_wait") == 1
    assert moments[-1].situation == "calm_play"


def test_near_box_free_kick_and_goal_kick_end_to_end():
    fk = S.run(300, 4, situation="free_kick", restart_pending=True, ball_zone="final_third",
               ball_xy=(88.0, 30.0), movement=0.0, situation_detail={"restart": "free_kick"})
    m, _ = drive(fk)
    assert [(x.situation, x.lock_rule) for x in m] == [("set_piece_wait", "on_restart")]
    far = S.run(300, 2, situation="free_kick", restart_pending=True, ball_xy=(30.0, 30.0))
    assert drive(far)[0][0].situation == "referee_decision"
    gk = S.run(400, 2, situation="goal_kick", restart_pending=True, movement=0.0)
    assert [(x.situation, x.cost) for x in drive(gk)[0]] == [("goal_kick", R.COST_STOPPAGE_RESTART)]


def test_attack_ending_soon_after_a_moment_still_waits_eight_seconds():
    states = S.run(100, 1) + S.run(101, 2, **S.ATTACK) + S.run(103, 15, last_attack_end_t=103.0)
    moments, _ = drive(states)
    assert [m.open_at for m in moments] == [100, 111, 115]


def test_g_exact_boundaries_and_stoppage():
    m, d = drive(S.run(2684, 3))
    assert [x.open_at for x in m] == [2684]
    assert [(x.t, x.blocked_by) for x in acts(d, "blocked")] == [(2685, "half_end")]
    m, d = drive(S.run(5384, 3, half=2))
    assert [x.open_at for x in m] == [5384] and acts(d, "blocked")[0].t == 5385
    m, _ = drive(S.run(2710, 10))  # first-half stoppage time
    assert m == []


def test_determinism_across_hash_seeds():
    import os
    import subprocess
    import sys
    from pathlib import Path
    code = (
        "import json\n"
        "from backend.agents.timing import RulePolicy, TimingAgent\n"
        "from backend.tests.fixtures import timing_scenarios as S\n"
        "a = TimingAgent(RulePolicy(), 'm1'); out = []\n"
        "for s in S.scenario_b_fast_attack() + S.scenario_e_corner() + S.scenario_a_calm_feed():\n"
        "    o = a.step(s)\n"
        "    out += [(m.moment_id, m.tone, m.open_at) for m in o.moments]\n"
        "    out += [d.to_contract() for d in o.decisions]\n"
        "print(json.dumps(out, sort_keys=True))\n")
    root = Path(__file__).resolve().parents[2]
    outs = [subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True,
                           check=True, env={**os.environ, "PYTHONHASHSEED": seed}).stdout
            for seed in ("1", "2")]
    assert outs[0] == outs[1] and len(outs[0]) > 100


# .............................................................................
# review gaps: stay-silent band, pinned thresholds, duplicate step
# .............................................................................


def test_stay_silent_band_holds_until_cost_drops_to_candidate():
    band = [S.st(102 + i, last_attack_end_t=101.0, **S.LINGER) for i in range(6)]
    assert R.CANDIDATE_COST < interruption_cost(band[0])[0] < R.SILENT_COST
    calm_again = S.run(108, 12, last_attack_end_t=101.0)
    moments, decisions = drive(S.run(100, 2, **S.ATTACK) + band + calm_again)
    assert [(d.action, d.t) for d in decisions if d.action in ("silent", "calm")] == [
        ("silent", 100), ("calm", 108)]
    assert moments[0].open_at == 109  # after_attack (8 s from 101) lifts at 109


def _with_cost(monkeypatch, cost):
    from backend.agents import timing as T
    real = T.interruption_cost
    monkeypatch.setattr(T, "interruption_cost", lambda s, g=None: (cost, real(s, g)[1]))


def test_threshold_values_are_pinned():
    assert (R.CANDIDATE_COST, R.SILENT_COST) == (0.45, 0.70)


def test_cost_045_is_candidate_and_046_is_not(monkeypatch):
    _with_cost(monkeypatch, 0.45)
    moments, decisions = drive(S.run(100, 2))
    assert len(moments) == 1 and decisions[0].action == "calm"
    _with_cost(monkeypatch, 0.46)
    moments, decisions = drive(S.run(100, 6))
    assert moments == [] and decisions == []


def test_cost_069_is_not_silent_and_070_is_(monkeypatch):
    _with_cost(monkeypatch, 0.69)
    assert drive(S.run(100, 3))[1] == []
    _with_cost(monkeypatch, 0.70)
    moments, decisions = drive(S.run(100, 3))
    assert moments == [] and [d.action for d in decisions] == ["silent"]


def test_duplicate_step_at_same_half_and_t_is_ignored():
    agent = TimingAgent(RulePolicy(), MATCH)
    first = agent.step(S.st(100))
    assert len(first.moments) == 1 and len(first.decisions) == 2
    dup = agent.step(S.st(100))
    assert dup.moments == () and dup.decisions == ()
    assert agent.step(S.st(101)).moments == ()  # spacing still counts from the first


def test_make_state_defaults_and_overrides():
    from backend.tests.fixtures.states import make_state
    a, b = make_state(), make_state(situation="throw_in", restart_pending=True)
    assert a.mode == "live" and a.dull_parts == {"possession": 0.5} and a.dull_parts is not \
        b.dull_parts
    assert b.situation == "throw_in" and b.restart_pending
    import pytest
    with pytest.raises(TypeError):
        make_state(not_a_field=1)
