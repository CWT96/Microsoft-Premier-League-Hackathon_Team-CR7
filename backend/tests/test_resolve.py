"""test_resolve.py — known-answer tests for the resolver registry, engine and 10 catalog rules.

Last Modified: 2026-10-24 22:00, first version.
Inputs:  hand-built MatchEvent sequences (no files, no network)
Outputs: none

Workflow:
  - one block per template: yes / no / void branches
  - engine behaviour: lock boundary, on_restart, amends, HT vs FT, registry extension

CAUTION: none
"""

from __future__ import annotations

import pytest

from backend.app.models import Option, Question
from backend.core.types import MatchEvent, Outcome, Player, Roster
from backend.resolve import Resolver, Verdict, register
from backend.resolve import registry

N, C = "NBR", "CAL"  # home, away


def make_roster() -> Roster:
    players = (
        Player("nbr-1", "Ward", N, "GK", 1),
        Player("nbr-5", "Stone", N, "DF", 5),
        Player("nbr-8", "Mensah", N, "MF", 8),
        Player("nbr-9", "Okoro", N, "FW", 9),
        Player("cal-1", "Fox", C, "GK", 1),
        Player("cal-9", "Reyes", C, "FW", 9),
    )
    return Roster(home=N, away=C, home_name="Northbridge", away_name="Calder", players=players)


_n = 0


def ev(t: float, type_: str, team: str = N, half: int = 1, **kw) -> MatchEvent:
    global _n
    _n += 1
    return MatchEvent(id=kw.pop("id", f"e{_n}"), t=t, half=half, team=team, type=type_, **kw)


def yes_no(template_id: str, qid: str = "q", category: str = "instant",
           resolve_by: float | str = 100.0, **kw) -> Question:
    return Question(qid=qid, category=category, template_id=template_id, text="?",
                    options=[Option(id="yes", label="Yes"), Option(id="no", label="No")],
                    open_at=0, lock_at=6, resolve_by=resolve_by, hint_available=False, **kw)


def pass_q(options: tuple[str, str] = ("forward", "sideways"), **params) -> Question:
    return Question(qid="q", category="instant", template_id="next_pass_dir", text="?",
                    options=[Option(id=o, label=o) for o in options], open_at=0, lock_at=6,
                    resolve_by=21.0, hint_available=True, params=params)


def run(question: Question, events: list[MatchEvent], lock_t: float = 100.0,
        end_t: float | None = None, half: int = 1) -> list[Outcome]:
    """Watch, then feed every event at its own second, and step to ``end_t``."""
    r = Resolver(make_roster())
    r.watch(question, lock_t)
    out: list[Outcome] = []
    end = end_t if end_t is not None else max([e.t for e in events] + [lock_t]) + 120
    t = int(lock_t)
    pending = sorted(events, key=lambda e: e.t)
    while t <= end:
        now = [e for e in pending if (t - 1 < e.t <= t) or (t == int(lock_t) and e.t <= t)]
        out += r.step(half, float(t), now)
        t += 1
    return out


def only(outs: list[Outcome]) -> Outcome:
    assert len(outs) == 1, outs
    return outs[0]


# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
#
# next_pass_dir
#
# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%


def test_next_pass_worked_example_forward() -> None:
    p = ev(104, "pass", player_id="nbr-8", start=(40, 30), end=(56, 28), outcome="complete",
           id="e1042")
    o = only(run(pass_q(), [p]))
    assert (o.status, o.correct_option, o.evidence) == ("resolved", "forward", ("e1042",))
    assert o.facts["dx_m"] == 16
    assert o.resolved_at == 104


@pytest.mark.parametrize("dx,expected", [(5, "sideways"), (5.1, "forward"), (-5, "sideways"),
                                         (-5.1, "backward"), (0, "sideways")])
def test_next_pass_dead_zone_edge(dx: float, expected: str) -> None:
    p = ev(104, "pass", start=(40, 30), end=(40 + dx, 30))
    q = pass_q((expected, "other"))
    assert only(run(q, [p])).correct_option == expected


def test_next_pass_custom_dead_zone() -> None:
    p = ev(104, "pass", start=(40, 30), end=(46, 30))
    q = pass_q(dead_zone_m=8.0)
    assert only(run(q, [p])).correct_option == "sideways"


def test_next_pass_excluded_third_outcome_is_void() -> None:
    p = ev(104, "pass", start=(40, 30), end=(30, 30))  # backward, not offered
    o = only(run(pass_q(), [p]))
    assert o.status == "void" and o.correct_option is None


@pytest.mark.parametrize("stop", ["foul", "out", "throw_in", "goal_kick", "injury", "half_end",
                                  "goal", "var_check", "corner"])
def test_next_pass_void_when_play_stops_first(stop: str) -> None:
    events = [ev(102, stop), ev(104, "pass", start=(40, 30), end=(60, 30))]
    o = only(run(pass_q(), events))
    assert (o.status, o.correct_option, o.evidence) == ("void", None, ())


def test_next_pass_void_on_timeout() -> None:
    o = only(run(pass_q(), [], end_t=130))
    assert o.status == "void"
    assert o.resolved_at == 115


def test_next_pass_pass_after_window_does_not_count() -> None:
    o = only(run(pass_q(), [ev(116, "pass", start=(40, 30), end=(60, 30))]))
    assert o.status == "void"


def test_lock_boundary_event_at_lock_t_does_not_count() -> None:
    at_lock = ev(100, "pass", start=(40, 30), end=(20, 30))  # backward, must be ignored
    later = ev(103, "pass", start=(40, 30), end=(60, 30))
    o = only(run(pass_q(), [at_lock, later]))
    assert o.correct_option == "forward" and o.evidence == (later.id,)


def test_incomplete_pass_still_counts_as_pass() -> None:
    p = ev(104, "pass", start=(40, 30), end=(60, 30), outcome="incomplete")
    assert only(run(pass_q(), [p])).correct_option == "forward"


def test_each_question_returned_once() -> None:
    r = Resolver(make_roster())
    r.watch(pass_q(), 100)
    p = ev(104, "pass", start=(40, 30), end=(60, 30))
    assert len(r.step(1, 104, [p])) == 1
    assert r.step(1, 105, []) == []
    assert r.step(1, 130, []) == []


# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
#
# on_restart lock
#
# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%


def test_on_restart_lock_uses_actual_lock_time() -> None:
    award = ev(95, "throw_in")  # awarded before the question locked
    kick = ev(112, "pass", start=(30, 30), end=(45, 30))  # taken at 110 (lock), kicked at 112
    q = Question(**{**pass_q().model_dump(), "lock_rule": "on_restart", "lock_at": 130})
    r = Resolver(make_roster())
    r.watch(q, 110.0)  # app passes the time the set piece was taken, not lock_at
    assert r.step(1, 110, [award]) == []
    o = only(r.step(1, 112, [kick]))
    assert o.correct_option == "forward"


# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
#
# keep_ball
#
# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%


def last_nbr_touch() -> MatchEvent:
    return ev(98, "pass", team=N, start=(30, 30), end=(40, 30))


def test_keep_ball_yes_when_no_turnover() -> None:
    events = [last_nbr_touch(), ev(105, "pass", start=(40, 30), end=(50, 30)),
              ev(110, "carry", start=(50, 30), end=(55, 30))]
    o = only(run(yes_no("keep_ball"), events))
    assert (o.status, o.correct_option, o.evidence) == ("resolved", "yes", ())
    assert o.facts == {"seconds_kept": 15}
    assert o.resolved_at == 115


def test_keep_ball_no_when_opponent_takes_it() -> None:
    steal = ev(107, "interception", team=C, id="steal")
    o = only(run(yes_no("keep_ball"), [last_nbr_touch(), steal]))
    assert (o.correct_option, o.evidence) == ("no", ("steal",))
    assert o.facts["seconds_kept"] == 7


def test_keep_ball_lost_tackle_by_possession_team_is_turnover() -> None:
    lost = ev(106, "tackle", team=N, outcome="lost")
    assert only(run(yes_no("keep_ball"), [last_nbr_touch(), lost])).correct_option == "no"


def test_keep_ball_out_to_opponent_is_turnover() -> None:
    events = [last_nbr_touch(), ev(106, "out", team=N), ev(107, "throw_in", team=C)]
    assert only(run(yes_no("keep_ball"), events)).correct_option == "no"


def test_keep_ball_restart_for_same_team_is_kept() -> None:
    events = [last_nbr_touch(), ev(106, "out", team=C), ev(107, "throw_in", team=N),
              ev(110, "pass", team=N, start=(30, 30), end=(40, 30))]
    assert only(run(yes_no("keep_ball"), events)).correct_option == "yes"


def test_keep_ball_foul_by_opponent_free_kick_for_us_is_kept() -> None:
    events = [last_nbr_touch(), ev(106, "foul", team=C), ev(107, "free_kick", team=N)]
    assert only(run(yes_no("keep_ball"), events)).correct_option == "yes"


def test_keep_ball_uses_team_param_over_last_touch() -> None:
    events = [last_nbr_touch(), ev(106, "pass", team=C, start=(30, 30), end=(40, 30))]
    assert only(run(yes_no("keep_ball", params={"team": C}), events)).correct_option == "yes"


def test_keep_ball_possession_from_restart_award_before_lock() -> None:
    events = [ev(99, "throw_in", team=C), ev(106, "pass", team=C, start=(10, 30), end=(20, 30))]
    assert only(run(yes_no("keep_ball"), events)).correct_option == "yes"


def test_keep_ball_void_when_play_stops() -> None:
    o = only(run(yes_no("keep_ball"), [last_nbr_touch(), ev(106, "injury")]))
    assert o.status == "void"


def test_keep_ball_void_without_known_possession() -> None:
    assert only(run(yes_no("keep_ball"), [])).status == "void"


# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
#
# reach_final_third
#
# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%


def test_reach_final_third_yes() -> None:
    carry = ev(110, "carry", start=(60, 30), end=(72, 30), id="c1")
    o = only(run(yes_no("reach_final_third"), [last_nbr_touch(), carry]))
    assert (o.correct_option, o.evidence) == ("yes", ("c1",))
    assert o.facts["seconds_to_reach"] == 10


def test_reach_final_third_x_exactly_70_is_not_enough() -> None:
    carry = ev(110, "carry", start=(60, 30), end=(70, 30))
    assert only(run(yes_no("reach_final_third"), [last_nbr_touch(), carry])).correct_option == "no"


def test_reach_final_third_no_on_turnover_before_reaching() -> None:
    events = [last_nbr_touch(), ev(105, "interception", team=C),
              ev(110, "carry", team=N, start=(60, 30), end=(80, 30))]
    assert only(run(yes_no("reach_final_third"), events)).correct_option == "no"


def test_reach_final_third_no_on_timeout() -> None:
    o = only(run(yes_no("reach_final_third"), [last_nbr_touch()], end_t=130))
    assert o.correct_option == "no" and o.resolved_at == 120


def test_reach_final_third_incomplete_pass_landing_does_not_count() -> None:
    p = ev(104, "pass", start=(60, 30), end=(80, 30), outcome="incomplete")
    assert only(run(yes_no("reach_final_third"), [last_nbr_touch(), p])).correct_option == "no"


def test_reach_final_third_void_on_stop() -> None:
    assert only(run(yes_no("reach_final_third"),
                    [last_nbr_touch(), ev(105, "var_check")])).status == "void"


# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
#
# shot_soon
#
# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%


def test_shot_soon_yes_by_either_team() -> None:
    shot = ev(150, "shot", team=C, id="s1")
    o = only(run(yes_no("shot_soon"), [shot]))
    assert (o.correct_option, o.evidence, o.facts["seconds_to_shot"]) == ("yes", ("s1",), 50)


def test_shot_soon_no_after_60s() -> None:
    o = only(run(yes_no("shot_soon"), [ev(161, "shot")], end_t=200))
    assert (o.correct_option, o.evidence, o.facts) == ("no", (), {"window_s": 60})
    assert o.resolved_at == 160


def test_shot_soon_foul_does_not_void() -> None:
    o = only(run(yes_no("shot_soon"), [ev(110, "foul"), ev(120, "shot")]))
    assert o.correct_option == "yes"


def test_shot_soon_void_when_half_ends_inside_window() -> None:
    assert only(run(yes_no("shot_soon"), [ev(130, "half_end")])).status == "void"


# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
#
# forecast templates
#
# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%


def run_full(question: Question, events: list[MatchEvent], ht: bool = False) -> list[Outcome]:
    r = Resolver(make_roster())
    r.watch(question, 6.0)
    first = [e for e in events if e.half == 1]
    second = [e for e in events if e.half == 2]
    out = r.step(1, 2700, first)
    out += r.on_half_end(1)
    if ht:
        return out
    out += r.step(2, 5400, second)
    out += r.on_full_end()
    return out


def goal(t: float, team: str, half: int = 1) -> MatchEvent:
    return ev(t, "goal", team=team, half=half)


def test_goals_over_yes_counts_both_halves() -> None:
    q = yes_no("goals_over", category="forecast", resolve_by="FT", params={"line": 3})
    o = only(run_full(q, [goal(500, N), goal(900, C), goal(3000, N, 2)]))
    assert (o.correct_option, o.facts) == ("yes", {"goals": 3, "line": 3})
    assert len(o.evidence) == 3


def test_goals_over_no() -> None:
    q = yes_no("goals_over", category="forecast", resolve_by="FT", params={"line": 3})
    o = only(run_full(q, [goal(500, N), goal(3000, N, 2)]))
    assert (o.correct_option, o.facts["goals"]) == ("no", 2)


def test_forecast_waits_for_its_whistle() -> None:
    q = yes_no("goals_over", category="forecast", resolve_by="FT", params={"line": 1})
    assert run_full(q, [goal(500, N)], ht=True) == []


def test_team_wins_branches() -> None:
    q = yes_no("team_wins", category="forecast", resolve_by="FT", params={"team": N})
    assert only(run_full(q, [goal(10, N), goal(20, C), goal(3000, N, 2)])).correct_option == "yes"
    assert only(run_full(q, [goal(10, C)])).correct_option == "no"
    drawn = only(run_full(q, [goal(10, N), goal(20, C)]))
    assert drawn.correct_option == "no" and drawn.facts == {"goals_for": 1, "goals_against": 1}


def test_card_second_half_ignores_first_half_cards() -> None:
    q = yes_no("card_second_half", category="forecast", resolve_by="FT")
    assert only(run_full(q, [ev(900, "card", outcome="yellow")])).correct_option == "no"
    c = ev(4000, "card", half=2, outcome="yellow", id="c2")
    o = only(run_full(q, [ev(900, "card", outcome="yellow"), c]))
    assert (o.correct_option, o.evidence, o.facts) == ("yes", ("c2",), {"count": 1})


def test_second_half_goal_ignores_first_half_goals() -> None:
    q = yes_no("second_half_goal", category="forecast", resolve_by="FT")
    assert only(run_full(q, [goal(900, N)])).correct_option == "no"
    o = only(run_full(q, [goal(4000, C, 2)]))
    assert (o.correct_option, o.facts, len(o.evidence)) == ("yes", {"count": 1}, 1)


# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
#
# player_stat_over: HT vs FT, stat by position
#
# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%


def stat_q(player_id: str, line: int, resolve_by: str) -> Question:
    return yes_no("player_stat_over", qid=f"q-{player_id}", category="player",
                  resolve_by=resolve_by, player_id=player_id, params={"line": line})


def test_player_stat_ht_counts_first_half_only() -> None:
    events = [ev(100, "shot", player_id="nbr-9"), ev(200, "shot", player_id="nbr-9"),
              ev(3000, "shot", half=2, player_id="nbr-9")]
    o = only(run_full(stat_q("nbr-9", 2, "HT"), events, ht=True))
    assert (o.correct_option, o.facts) == ("yes", {"count": 2, "line": 2, "stat": "shots"})
    assert len(o.evidence) == 2


def test_player_stat_ft_counts_second_half_only() -> None:
    events = [ev(100, "shot", player_id="nbr-9"), ev(200, "shot", player_id="nbr-9"),
              ev(3000, "shot", half=2, player_id="nbr-9")]
    o = only(run_full(stat_q("nbr-9", 2, "FT"), events))
    assert (o.correct_option, o.facts["count"]) == ("no", 1)


@pytest.mark.parametrize("pid,etype,kw", [
    ("nbr-1", "save", {}),
    ("nbr-5", "clearance", {}),
    ("nbr-8", "pass", {"outcome": "complete"}),
    ("nbr-9", "shot", {}),
])
def test_player_stat_by_position(pid: str, etype: str, kw: dict) -> None:
    hits = [ev(100 + i, etype, player_id=pid, **kw) for i in range(2)]
    noise = [ev(300, "pass", player_id=pid, outcome="incomplete"),
             ev(301, "tackle", player_id=pid), ev(302, "pass", team=C, player_id="cal-9",
                                                  outcome="complete")]
    o = only(run_full(stat_q(pid, 2, "HT"), hits + noise, ht=True))
    assert (o.correct_option, o.facts["count"]) == ("yes", 2)


def test_player_stat_never_void_with_zero_events() -> None:
    o = only(run_full(stat_q("nbr-5", 1, "HT"), [], ht=True))
    assert (o.status, o.correct_option, o.facts["count"]) == ("resolved", "no", 0)


# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
#
# player_touch_window
#
# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%


def touch_q(window_s: int = 60) -> Question:
    return yes_no("player_touch_window", category="player", player_id="nbr-8",
                  qid="q-nbr-8", params={"window_s": window_s})


def test_touch_window_yes_on_any_event_of_player() -> None:
    t = ev(130, "tackle", player_id="nbr-8", id="t1")
    o = only(run(touch_q(), [ev(110, "pass", player_id="nbr-9"), t]))
    assert (o.correct_option, o.evidence, o.facts["touches"]) == ("yes", ("t1",), 1)


def test_touch_window_no_when_window_ends() -> None:
    o = only(run(touch_q(30), [ev(131, "pass", player_id="nbr-8")], end_t=200))
    assert (o.status, o.correct_option, o.facts["touches"]) == ("resolved", "no", 0)
    assert o.resolved_at == 130


def test_touch_window_substituted_off_counts_as_no() -> None:
    sub = ev(110, "substitution", player_id="nbr-8", related_player_id="nbr-14", id="sub")
    late = ev(120, "pass", player_id="nbr-8")  # data glitch after the substitution
    o = only(run(touch_q(), [sub, late]))
    assert (o.status, o.correct_option) == ("resolved", "no")
    assert o.evidence == ("sub",)


def test_touch_window_never_void_when_half_ends() -> None:
    o = only(run(touch_q(), [ev(120, "half_end")]))
    assert (o.status, o.correct_option) == ("resolved", "no")


def test_touch_window_event_at_lock_does_not_count() -> None:
    o = only(run(touch_q(30), [ev(100, "pass", player_id="nbr-8")], end_t=200))
    assert o.correct_option == "no"


# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
#
# amends
#
# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%


def test_amend_re_resolves_settled_question() -> None:
    r = Resolver(make_roster())
    r.watch(pass_q(("forward", "backward")), 100)
    first = ev(104, "pass", start=(40, 30), end=(60, 30), id="p1")
    assert only(r.step(1, 104, [first])).correct_option == "forward"
    fix = ev(104, "pass", start=(40, 30), end=(25, 30), amends="p1", id="p1b")
    again = only(r.step(1, 140, [fix]))
    assert (again.qid, again.correct_option, again.evidence) == ("q", "backward", ("p1b",))
    assert r.step(1, 141, []) == []


def test_amend_before_settling_just_replaces_the_event() -> None:
    r = Resolver(make_roster())
    r.watch(pass_q(("forward", "backward")), 100)
    outs = r.step(1, 104, [ev(104, "pass", start=(40, 30), end=(60, 30), id="p1"),
                           ev(104, "pass", start=(40, 30), end=(20, 30), amends="p1", id="p1b")])
    assert only(outs).correct_option == "backward"


def test_amend_of_goal_changes_forecast() -> None:
    q = yes_no("goals_over", category="forecast", resolve_by="FT", params={"line": 1})
    r = Resolver(make_roster())
    r.watch(q, 6.0)
    r.step(1, 600, [ev(500, "goal", id="g1")])
    first = only(r.on_full_end())
    assert first.correct_option == "yes"
    disallowed = ev(500, "var_check", amends="g1", id="g1v")
    second = only(r.step(2, 5400, [disallowed]))
    assert (second.qid, second.correct_option, second.facts["goals"]) == ("q", "no", 0)


def test_amend_unrelated_event_does_not_reemit() -> None:
    r = Resolver(make_roster())
    r.watch(pass_q(), 100)
    only(r.step(1, 104, [ev(104, "pass", start=(40, 30), end=(60, 30), id="p1")]))
    other = ev(500, "pass", start=(40, 30), end=(60, 30), id="x1")
    r.step(1, 500, [other])
    assert r.step(1, 501, [ev(500, "pass", amends="x1", id="x2")]) == []


# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
#
# registry
#
# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%


def test_all_ten_catalog_templates_registered() -> None:
    expected = {"next_pass_dir", "keep_ball", "reach_final_third", "shot_soon", "goals_over",
                "team_wins", "card_second_half", "second_half_goal", "player_stat_over",
                "player_touch_window"}
    assert expected <= set(registry.RULES)


def test_register_new_template_is_one_function_and_one_call(monkeypatch) -> None:
    monkeypatch.setattr(registry, "RULES", dict(registry.RULES))

    def corner_soon(question, ctx):
        for e in ctx.after_lock():
            if e.type == "corner":
                return Verdict("yes", (e.id,), {"seconds_to_corner": int(e.t - ctx.lock[1])}, e.t)
        return None

    register("corner_soon", corner_soon)
    q = yes_no("corner_soon")
    o = only(run(q, [ev(110, "corner", id="k1")]))
    assert (o.correct_option, o.evidence, o.facts) == ("yes", ("k1",), {"seconds_to_corner": 10})


def test_register_duplicate_raises(monkeypatch) -> None:
    monkeypatch.setattr(registry, "RULES", dict(registry.RULES))
    with pytest.raises(ValueError):
        register("keep_ball", lambda q, c: None)


def test_watch_unknown_template_raises() -> None:
    r = Resolver(make_roster())
    with pytest.raises(ValueError):
        r.watch(yes_no("no_such_template"), 10)


def test_watch_same_qid_twice_raises() -> None:
    r = Resolver(make_roster())
    r.watch(pass_q(), 10)
    with pytest.raises(ValueError):
        r.watch(pass_q(), 10)


def test_second_half_lock_uses_half_of_last_step() -> None:
    r = Resolver(make_roster())
    r.step(2, 2800, [])
    r.watch(pass_q(), 2800)
    p = ev(2803, "pass", half=2, start=(40, 30), end=(60, 30))
    assert only(r.step(2, 2803, [p])).correct_option == "forward"


def test_first_half_stoppage_event_does_not_count_for_second_half_question() -> None:
    r = Resolver(make_roster())
    r.watch(pass_q(), 2710.0, lock_half=2)  # t=2710 in half 2
    late_h1 = ev(2715, "pass", half=1, start=(40, 30), end=(60, 30))  # half 1 stoppage clock
    assert r.step(2, 2711, [late_h1]) == []


# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
#
# Review fixes: validation, robustness
#
# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%


@pytest.mark.parametrize("question", [
    yes_no("goals_over", category="forecast", resolve_by="FT"),
    yes_no("goals_over", category="forecast", resolve_by="FT", params={"line": "3"}),
    yes_no("team_wins", category="forecast", resolve_by="FT"),
    yes_no("team_wins", category="forecast", resolve_by="FT", params={"team": "XXX"}),
    yes_no("keep_ball", params={"team": "XXX"}),
    yes_no("reach_final_third", params={"team": "XXX"}),
    pass_q(dead_zone_m=-1),
    yes_no("player_touch_window", category="player", params={"window_s": 30}),
    yes_no("player_touch_window", category="player", player_id="ghost", params={"window_s": 30}),
    yes_no("player_touch_window", category="player", player_id="nbr-8"),
    yes_no("player_touch_window", category="player", player_id="nbr-8", params={"window_s": 0}),
    yes_no("player_stat_over", category="player", resolve_by="HT", player_id="nbr-8"),
    yes_no("player_stat_over", category="player", resolve_by=50.0, player_id="nbr-8",
           params={"line": 1}),
    yes_no("player_stat_over", category="player", resolve_by="HT", player_id="ghost",
           params={"line": 1}),
])
def test_watch_rejects_bad_params(question: Question) -> None:
    with pytest.raises(ValueError):
        Resolver(make_roster()).watch(question, 10)


def test_line_zero_is_a_value_not_missing() -> None:
    q = yes_no("goals_over", category="forecast", resolve_by="FT", params={"line": 0})
    o = only(run_full(q, []))
    assert (o.correct_option, o.facts) == ("yes", {"goals": 0, "line": 0})


def test_faulty_rule_voids_only_itself(monkeypatch) -> None:
    monkeypatch.setattr(registry, "RULES", dict(registry.RULES))

    def broken(question, ctx):
        raise KeyError("boom")

    register("broken_t", broken)
    r = Resolver(make_roster())
    r.watch(yes_no("broken_t", qid="bad"), 100)
    r.watch(pass_q(), 100)
    outs = r.step(1, 104, [ev(104, "pass", start=(40, 30), end=(60, 30))])
    by_qid = {o.qid: o for o in outs}
    assert by_qid["bad"].status == "void"
    assert by_qid["q"].correct_option == "forward"
    assert r.step(1, 105, []) == []  # the faulty one is not retried


# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
#
# Review fixes: possession rules
#
# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%


@pytest.mark.parametrize("template", ["keep_ball", "reach_final_third"])
def test_goal_by_possession_team_is_yes(template: str) -> None:
    g = ev(108, "goal", team=N, id="g")
    o = only(run(yes_no(template), [last_nbr_touch(), g]))
    assert (o.status, o.correct_option, o.evidence) == ("resolved", "yes", ("g",))


@pytest.mark.parametrize("template", ["keep_ball", "reach_final_third"])
def test_goal_by_opponent_is_no(template: str) -> None:
    o = only(run(yes_no(template), [last_nbr_touch(), ev(108, "goal", team=C)]))
    assert (o.status, o.correct_option) == ("resolved", "no")


def test_hoofed_clearance_does_not_reach_final_third() -> None:
    clear = ev(104, "clearance", start=(20, 30), end=(85, 30))
    assert only(run(yes_no("reach_final_third"), [last_nbr_touch(), clear])).correct_option == "no"


def test_complete_pass_landing_in_final_third_counts() -> None:
    p = ev(104, "pass", start=(60, 30), end=(80, 30), outcome="complete", id="pp")
    o = only(run(yes_no("reach_final_third"), [last_nbr_touch(), p]))
    assert (o.correct_option, o.evidence) == ("yes", ("pp",))


@pytest.mark.parametrize("template", ["shot_soon", "reach_final_third"])
def test_half_change_without_half_end_event_voids(template: str) -> None:
    r = Resolver(make_roster())
    events = [ev(2690, "pass", start=(30, 30), end=(40, 30))] if template != "shot_soon" else []
    r.watch(yes_no(template), 2690.0)
    r.step(1, 2690, events)
    o = only(r.step(2, 2701, []))
    assert o.status == "void"


@pytest.mark.parametrize("template", ["shot_soon", "reach_final_third"])
def test_on_half_end_voids_open_window(template: str) -> None:
    r = Resolver(make_roster())
    r.watch(yes_no(template), 2690.0)
    r.step(1, 2690, [ev(2690, "pass", start=(30, 30), end=(40, 30))])
    r.step(1, 2695, [])
    assert only(r.on_half_end(1)).status == "void"


def test_shot_soon_void_by_full_end() -> None:
    assert only(run(yes_no("shot_soon"), [ev(130, "full_end", half=1)])).status == "void"


@pytest.mark.parametrize("restart,start,expected", [
    ("corner", None, "yes"), ("free_kick", (80, 30), "yes"), ("free_kick", (50, 30), "no"),
])
def test_attacking_restart_first_after_lock(restart: str, start, expected: str) -> None:
    events = [last_nbr_touch(), ev(103, "out", team=C), ev(104, restart, team=N, start=start,
                                                           id="rs")]
    o = only(run(yes_no("reach_final_third"), events))
    assert o.correct_option == expected
    if expected == "yes":
        assert o.evidence == ("rs",)


def test_corner_later_in_window_is_not_the_first_event() -> None:
    events = [last_nbr_touch(), ev(103, "pass", start=(30, 30), end=(45, 30)),
              ev(106, "corner", team=N)]
    assert only(run(yes_no("reach_final_third"), events)).correct_option == "no"


def test_possession_lost_by_opposition_tackle_won() -> None:
    won = ev(106, "tackle", team=C, outcome="won", id="tk")
    o = only(run(yes_no("keep_ball"), [last_nbr_touch(), won]))
    assert (o.correct_option, o.evidence) == ("no", ("tk",))


def test_team_param_overrides_last_touch() -> None:
    n_pass = ev(105, "pass", team=N, start=(30, 30), end=(40, 30), id="np")
    o = only(run(yes_no("keep_ball", params={"team": C}), [last_nbr_touch(), n_pass]))
    assert (o.correct_option, o.evidence) == ("no", ("np",))


@pytest.mark.parametrize("template", ["keep_ball", "reach_final_third"])
def test_opponent_restart_after_lock_is_turnover(template: str) -> None:
    o = only(run(yes_no(template), [last_nbr_touch(), ev(106, "goal_kick", team=C)]))
    assert o.correct_option == "no"


# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
#
# Review fixes: player touches, boundaries, on_restart
#
# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%


def test_card_and_injury_are_not_touches() -> None:
    events = [ev(110, "card", player_id="nbr-8", outcome="yellow"),
              ev(112, "injury", player_id="nbr-8")]
    assert only(run(touch_q(30), events)).correct_option == "no"


def test_card_then_real_touch_is_yes() -> None:
    events = [ev(110, "card", player_id="nbr-8"), ev(115, "pass", player_id="nbr-8", id="pt")]
    o = only(run(touch_q(30), events))
    assert (o.correct_option, o.evidence) == ("yes", ("pt",))


def test_deadline_is_inclusive_for_every_window() -> None:
    p = ev(115, "pass", start=(40, 30), end=(60, 30))
    assert only(run(pass_q(), [p])).correct_option == "forward"
    steal = ev(115, "interception", team=C)
    assert only(run(yes_no("keep_ball"), [last_nbr_touch(), steal])).correct_option == "no"
    reach = ev(120, "carry", start=(60, 30), end=(80, 30))
    assert only(run(yes_no("reach_final_third"), [last_nbr_touch(), reach])).correct_option == "yes"
    assert only(run(yes_no("shot_soon"), [ev(160, "shot")])).correct_option == "yes"
    assert only(run(touch_q(30), [ev(130, "pass", player_id="nbr-8")])).correct_option == "yes"


def test_event_exactly_at_lock_is_ignored_by_possession_and_shot_rules() -> None:
    steal = ev(100, "interception", team=C)
    assert only(run(yes_no("keep_ball", params={"team": N}), [steal])).correct_option == "yes"
    carry = ev(100, "carry", start=(60, 30), end=(80, 30))
    assert only(run(yes_no("reach_final_third", params={"team": N}), [carry])
                ).correct_option == "no"
    assert only(run(yes_no("shot_soon"), [ev(100, "shot")])).correct_option == "no"


def test_on_restart_kick_at_lock_t_is_excluded_and_lock_at_gives_other_answer() -> None:
    award = ev(95, "throw_in")
    kick = ev(110, "pass", start=(30, 30), end=(45, 30))  # forward, at the lock
    nxt = ev(114, "pass", start=(45, 30), end=(30, 30))  # backward
    q = Question(**{**pass_q(("forward", "backward")).model_dump(),
                    "lock_rule": "on_restart", "lock_at": 130})
    assert only(run(q, [award, kick, nxt], lock_t=110)).correct_option == "backward"
    later = ev(140, "pass", start=(45, 30), end=(30, 30))
    kick2 = ev(111, "pass", start=(30, 30), end=(45, 30))
    assert only(run(q, [award, kick2, later], lock_t=110)).correct_option == "forward"
    assert only(run(q, [award, kick2, later], lock_t=130)).correct_option == "backward"


# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
#
# Review fixes: forecasts and engine
#
# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%


def test_team_wins_away_team_and_goalless_draw() -> None:
    q = yes_no("team_wins", category="forecast", resolve_by="FT", params={"team": C})
    o = only(run_full(q, [goal(10, C), goal(3000, C, 2), goal(20, N)]))
    assert (o.correct_option, o.facts) == ("yes", {"goals_for": 2, "goals_against": 1})
    o = only(run_full(q, []))
    assert (o.correct_option, o.facts) == ("no", {"goals_for": 0, "goals_against": 0})


def test_ht_forecast_settles_at_half_end_and_not_again() -> None:
    r = Resolver(make_roster())
    r.watch(stat_q("nbr-9", 1, "HT"), 6.0)
    r.step(1, 2700, [ev(100, "shot", player_id="nbr-9")])
    assert only(r.on_half_end(1)).correct_option == "yes"
    assert r.on_half_end(2) == [] and r.on_full_end() == []


def test_ft_forecast_stays_open_through_half_end() -> None:
    q = yes_no("goals_over", category="forecast", resolve_by="FT", params={"line": 1})
    r = Resolver(make_roster())
    r.watch(q, 6.0)
    r.step(1, 2700, [goal(100, N)])
    assert r.on_half_end(1) == []
    assert only(r.on_full_end()).correct_option == "yes"


def test_player_ft_question_asked_in_half_two() -> None:
    r = Resolver(make_roster())
    r.step(2, 2701, [ev(100, "shot", player_id="nbr-9"), ev(101, "shot", player_id="nbr-9")])
    r.watch(stat_q("nbr-9", 1, "FT"), 2701.0)
    r.step(2, 5000, [ev(3000, "shot", half=2, player_id="nbr-9")])
    o = only(r.on_full_end())
    assert (o.correct_option, o.facts["count"]) == ("yes", 1)


def test_half_and_full_end_close_open_instants() -> None:
    r = Resolver(make_roster())
    r.watch(pass_q(), 2690.0)
    r.watch(Question(**{**touch_q(60).model_dump(), "qid": "t"}), 2690.0)
    r.step(1, 2692, [])
    outs = {o.qid: o for o in r.on_half_end(1)}
    assert outs["q"].status == "void"
    assert (outs["t"].status, outs["t"].correct_option) == ("resolved", "no")
    r.watch(Question(**{**pass_q().model_dump(), "qid": "late"}), 5300.0,
            lock_half=2)
    assert only(r.on_full_end()).status == "void"


def test_on_half_end_keeps_second_half_instants_open() -> None:
    r = Resolver(make_roster())
    r.step(2, 2800, [])
    r.watch(pass_q(), 2800.0)
    assert r.on_half_end(1) == []


def test_multi_second_jump_in_one_step() -> None:
    r = Resolver(make_roster())
    r.watch(pass_q(), 100)
    o = only(r.step(1, 300, [ev(104, "pass", start=(40, 30), end=(60, 30))]))
    assert (o.correct_option, o.resolved_at) == ("forward", 104)
    r2 = Resolver(make_roster())
    r2.watch(yes_no("keep_ball"), 100)
    o = only(r2.step(1, 300, [last_nbr_touch()]))
    assert (o.correct_option, o.resolved_at) == ("yes", 115)


def test_next_pass_missing_coordinates_is_void() -> None:
    assert only(run(pass_q(), [ev(104, "pass")])).status == "void"


# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
#
# Review fixes: amends
#
# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%


def test_chained_amends_in_one_step_emit_once() -> None:
    r = Resolver(make_roster())
    r.watch(pass_q(("forward", "backward")), 100)
    only(r.step(1, 104, [ev(104, "pass", start=(40, 30), end=(60, 30), id="a1")]))
    outs = r.step(1, 105, [ev(104, "pass", start=(40, 30), end=(20, 30), amends="a1", id="a2"),
                           ev(104, "pass", start=(40, 30), end=(60, 30), amends="a2", id="a3")])
    o = only(outs)
    assert (o.correct_option, o.evidence) == ("forward", ("a3",))


def test_amend_that_leaves_question_undecided_keeps_old_result() -> None:
    r = Resolver(make_roster())
    r.watch(pass_q(), 100)
    only(r.step(1, 104, [ev(104, "pass", start=(40, 30), end=(60, 30), id="a1")]))
    assert r.step(1, 105, [ev(104, "carry", amends="a1", id="a2")]) == []
    assert r.step(1, 140, []) == []  # not reopened and voided by timeout


def test_amend_of_unknown_target_is_applied_as_new_event() -> None:
    r = Resolver(make_roster())
    r.watch(pass_q(), 100)
    p = ev(104, "pass", start=(40, 30), end=(60, 30), amends="never-seen", id="n1")
    assert only(r.step(1, 104, [p])).correct_option == "forward"


def test_redelivery_of_amended_away_event_is_ignored() -> None:
    q = yes_no("goals_over", category="forecast", resolve_by="FT", params={"line": 1})
    r = Resolver(make_roster())
    r.watch(q, 6.0)
    g1 = ev(500, "goal", id="g1")
    r.step(1, 600, [g1])
    assert only(r.on_full_end()).correct_option == "yes"
    only(r.step(2, 5400, [ev(500, "var_check", amends="g1", id="g1v")]))
    assert r.step(2, 5401, [g1]) == []
    r.watch(Question(**{**q.model_dump(), "qid": "q2"}), 6.0, lock_half=1)
    o = only(r.on_full_end())
    assert (o.qid, o.correct_option, o.facts["goals"]) == ("q2", "no", 0)
