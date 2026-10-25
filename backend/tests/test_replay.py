"""test_replay.py — Room: lobby, cards, locks, results, pauses, skip, hints, full time.

Last Modified: 2026-10-24 23:59, first version
Inputs:  hand-built tiny match and fake model (fixtures/replay_fixtures.py)
Outputs: none

Workflow:
  - drive a Room tick by tick on a fake clock and read the messages each member received
  - every payload with a contract schema is validated against contracts/

CAUTION: none
"""

from __future__ import annotations

import pytest

from backend.agents import timing_rules as TR
from backend.core.types import MatchEvent
from backend.tests.contract_utils import validate
from backend.tests.fixtures.replay_fixtures import (
    ATTACK_START, HT_T, WATER_T, Harness, tiny_states)
from backend.tests.fixtures.timing_scenarios import run, st

SCHEMAS = {"question.open": "question", "question.result": "result", "score.final": "score",
           "match.state": "state", "match.tracking": "tracking", "match.event": "event",
           "timing.decision": "timing_decision", "nudge": "nudge"}


def validate_all(log: list[dict]) -> None:
    for m in log:
        if m["type"] in SCHEMAS:
            validate(SCHEMAS[m["type"]], m["data"])


def started(h: Harness) -> Harness:
    """Play the lobby second, then let the host start the replay."""
    h.tick()
    h.room.control("u0", "resume")
    return h


def open_after_lobby() -> Harness:
    h = Harness()
    h.tick()  # lobby state: kickoff stack and pause
    return h


# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
#
# Lobby and kickoff stack
#
# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%


def test_session_message_has_host_and_bot_friends():
    h = Harness()
    session = h.msgs("session")[0]
    assert session["is_host"] is True and session["room_id"] == "r-test"
    assert session["match_id"] == "tiny" and len(session["friends"]) == 3


def test_lobby_shows_kickoff_stack_and_waits_for_host():
    h = open_after_lobby()
    first = h.msgs("question.open")
    assert len(first) == 1 and first[0]["qid"].startswith("q-")
    states = h.msgs("match.state")
    assert states[-1]["mode"] == "lobby" and states[-1]["t"] == 0
    for _ in range(3):  # paused: same t, one state per real second
        assert h.tick() == 1.0
    assert {s["t"] for s in h.msgs("match.state")} == {0}
    assert len(h.msgs("match.state")) == 4


def test_answering_a_stack_card_delivers_the_next_one():
    h = open_after_lobby()
    q1 = h.msgs("question.open")[0]
    assert h.room.answer("u0", q1["qid"], q1["options"][0]["id"]) is True
    qs = h.msgs("question.open")
    assert len(qs) == 2 and qs[1]["qid"] != q1["qid"]
    q2 = qs[1]
    h.room.answer("u0", q2["qid"], q2["options"][0]["id"])
    q3 = h.msgs("question.open")[2]
    assert q3["category"] == "player" and q3["player_id"] == "nbr-1-ward"
    assert q3["qid"].endswith("-nbr-1-ward")


def test_resume_locks_stack_and_starts_the_replay():
    h = open_after_lobby()
    qid = h.msgs("question.open")[0]["qid"]
    assert h.room.control("u0", "resume") is True
    assert {"qid": qid} in h.msgs("question.locked")
    assert h.room.pause is None
    h.tick()
    assert h.msgs("match.state")[-1]["t"] == 1
    assert h.msgs("match.state")[-1]["mode"] in ("live", "game")


def test_stack_locks_after_60_real_seconds():
    h = open_after_lobby()
    qid = h.msgs("question.open")[0]["qid"]
    h.clock.t += 57  # the lobby tick already advanced the clock by 1 s
    h.tick()
    assert h.msgs("question.locked") == []
    h.clock.t += 3
    h.tick()
    assert {"qid": qid} in h.msgs("question.locked")
    assert h.room.answer("u0", qid, "yes") is False


def test_only_host_may_send_controls():
    h = Harness(humans=2)
    h.tick()
    assert h.room.control("u1", "resume") is False
    assert h.room.pause is not None
    assert h.room.control("u0", "speed", 99) is False
    assert h.room.control("u0", "speed", 8) is True and h.room.speed == 8.0
    assert h.room.control("u0", "resume") is True


# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
#
# Calm play, locks, results, catch-up
#
# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%


def state_t_at_each_message(h: Harness) -> list[tuple[str, dict, float]]:
    """(type, data, t of the match.state that closes the same tick) for every message."""
    out, t = [], float("inf")
    for m in reversed(h.log):
        if m["type"] == "match.state":
            t = m["data"]["t"]
        out.append((m["type"], m["data"], t))
    return out[::-1]


def test_calm_moments_reach_the_user_and_attack_locks_the_open_card():
    h = Harness()
    h.play(answer=False)
    seq = state_t_at_each_message(h)
    opens = [(d, t) for ty, d, t in seq if ty == "question.open" and d["category"] == "instant"]
    assert len(opens) >= 3
    locks = [(d, t) for ty, d, t in seq if ty == "question.locked"]
    assert any(t == ATTACK_START for _, t in locks)  # early lock at attack start
    for d, t in opens:  # no card while the attack is on
        assert not ATTACK_START <= t < ATTACK_START + 4
    validate_all(h.log)


def test_result_arrives_after_attack_lock_and_matches_contract():
    h = Harness()
    h.play(answer=False)
    results = h.msgs("question.result")
    assert results
    locked = {m["qid"] for m in h.msgs("question.locked")}
    for r in results:
        assert r["qid"] in locked and "source" not in r
        assert r["you"]["answered"] is False
        assert set(r["crowd_split"]) <= {"global", "room", "friends"}
        assert "friends" in r["crowd_split"] or r["status"] == "void"
    validate_all(h.log)


def test_answered_result_carries_you_and_streak():
    h = Harness()
    h.play()
    mine = [r for r in h.msgs("question.result") if r["you"]["answered"]]
    assert mine
    assert all(r["you"]["streak"] >= 0 for r in mine)
    assert any(r["you"]["correct"] is True for r in mine) or any(
        r["status"] == "void" for r in mine)
    assert all("room" in r["crowd_split"] for r in mine)


def test_catchup_card_after_attack_ends():
    h = Harness()
    h.play()
    cards = h.msgs("catchup.card")
    assert len(cards) == 1
    c = cards[0]
    assert "source" not in c
    assert {"card_id", "t_start", "t_end", "event_ids", "headline", "explanation",
            "facts"} <= set(c)
    assert "s1" in c["event_ids"] or "s2" in c["event_ids"]


def test_tracking_and_events_are_broadcast_in_contract_shape():
    h = Harness()
    h.play()
    assert len(h.msgs("match.tracking")) == 1
    assert any(e["type"] == "shot" for e in h.msgs("match.event"))
    assert h.msgs("timing.decision")
    validate_all(h.log)


def test_late_and_repeated_answers_are_rejected():
    h = Harness()
    h.play(until=lambda: bool(h.room.members["u0"].open and
                              h.room.members["u0"].open.moment.kind == "instant"),
           answer=False)
    card = h.room.members["u0"].open
    q = card.question
    assert h.room.answer("u0", q.qid, "nonsense") is False
    assert h.room.answer("u0", "q-999", q.options[0].id) is False
    assert h.room.answer("u0", q.qid, q.options[0].id) is True
    assert h.room.answer("u0", q.qid, q.options[1].id) is False  # one answer per qid
    assert card.option == q.options[0].id


def test_answer_after_lock_is_rejected():
    h = Harness()
    h.play(until=lambda: bool(h.room.members["u0"].open and
                              h.room.members["u0"].open.moment.kind == "instant"),
           answer=False)
    card = h.room.members["u0"].open
    while not card.locked:
        h.tick()
    assert h.room.answer("u0", card.question.qid, card.question.options[0].id) is False


def test_speed_is_one_in_calm_play_and_host_speed_in_attack():
    h = started(Harness())
    delays = []
    while (d := h.tick()) is not None and h.room.state.t < ATTACK_START + 2:
        delays.append((h.room.state.t, d))
    assert all(d == 1.0 for t, d in delays if t < ATTACK_START)
    assert any(d == pytest.approx(1 / 15) for t, d in delays if t >= ATTACK_START)
    assert h.msgs("match.state")[-1]["speed"] == 15


# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
#
# Pauses: water break and half time
#
# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%


def play_to_pause(h: Harness, kind_t: int) -> None:
    while not (h.room.pause is not None and h.room.pause.kind != "lobby"):
        if h.room.pause is not None:
            h.room.control("u0", "resume")
        h.tick()
        for uid in h.ids:
            card = h.room.members[uid].open
            if card is not None:
                h.room.answer(uid, card.question.qid, card.question.options[0].id)


def test_water_break_pauses_with_a_card_and_times_out_after_30_s():
    h = Harness()
    play_to_pause(h, WATER_T)
    assert h.room.pause.kind == "water_break" and h.room.state.t == WATER_T
    seen_before = len(h.msgs("match.state"))
    wb = [m for m in h.room.open_moments if m.kind == "water_break"]
    assert len(wb) == 1
    h.tick()
    last = h.msgs("match.state")[-1]
    assert last["mode"] == "break" and last["break_kind"] == "water_break"
    assert last["t"] == WATER_T and len(h.msgs("match.state")) == seen_before + 1
    h.clock.t += TR.WATER_BREAK_WINDOW_S + 1
    h.tick()
    assert h.room.pause is None
    assert not [m for m in h.room.open_moments if m.kind == "water_break"]
    h.tick()
    assert h.msgs("match.state")[-1]["t"] == WATER_T + 1  # the match clock continues


def test_water_break_card_reaches_a_user_and_resume_locks_it():
    h = Harness()
    play_to_pause(h, WATER_T)
    # play_to_pause answers cards, so a break card was answered or is still open
    opens = [q for q in h.msgs("question.open") if q["lock_at"] - q["open_at"] == 30]
    assert len(opens) == 1 and opens[0]["category"] == "forecast"
    h.room.control("u0", "resume")
    assert h.room.pause is None
    assert {"qid": opens[0]["qid"]} in h.msgs("question.locked")


def test_half_time_stack_pauses_until_resume_or_60_s():
    h = Harness()
    while not (h.room.pause is not None and h.room.pause.kind == "half_time"):
        if h.room.pause is not None:
            h.room.control("u0", "resume")
        h.tick()
        for uid in h.ids:
            card = h.room.members[uid].open
            if card is not None:
                h.room.answer(uid, card.question.qid, card.question.options[0].id)
    assert h.room.state.t == HT_T
    hts = [q for q in h.msgs("question.open") if q["lock_at"] - q["open_at"] == 60]
    assert hts
    h.clock.t += 61
    assert h.tick() == 0.0 and h.room.pause is None
    h.tick()
    assert h.msgs("match.state")[-1]["half"] == 2
    assert h.msgs("match.state")[-1]["t"] == 2700


# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
#
# Full time, scores, leaderboards
#
# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%


def test_full_time_sends_score_and_three_leaderboards():
    h = Harness()
    h.play()
    assert h.room.done and h.tick() is None
    finals = h.msgs("score.final")
    assert len(finals) == 1 and finals[0]["user_id"] == "u0"
    assert finals[0]["tier"] in ("Rookie", "Rising Star", "Super Fan")
    assert finals[0]["summary"] and finals[0]["answered"] > 0
    assert h.finals["u0"] == finals[0]
    boards = {b["scope"]: b for b in h.msgs("leaderboard")}
    assert set(boards) == {"room", "friends", "global"}
    assert len(boards["friends"]["entries"]) == 4
    assert len(boards["global"]["entries"]) <= 11
    for b in boards.values():
        assert sum(e["is_you"] for e in b["entries"]) == 1
    assert h.msgs("match.state")[-1]["mode"] == "full_time"
    validate_all(h.log)


def test_every_message_in_a_full_run_validates():
    h = Harness(humans=2)
    h.play()
    for uid in h.ids:
        validate_all(h.logs[uid])
        assert len(h.msgs("score.final", uid)) == 1


# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
#
# Hints, nudge, player moments, skip
#
# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%


def test_hint_reveals_ai_pick_and_marks_the_card():
    h = open_after_lobby()
    q = h.msgs("question.open")[0]
    assert h.room.hint_request("u0", "q-nope") is False
    assert h.room.hint_request("u0", q["qid"]) is True
    hint = h.msgs("hint")[0]
    assert hint["qid"] == q["qid"] and hint["ai_pick"] in {o["id"] for o in q["options"]}
    assert 0 <= hint["p"] <= 1
    h.room.answer("u0", q["qid"], hint["ai_pick"])
    answers = h.room._answers(h.room.members["u0"])
    assert answers[0].used_hint is True and answers[0].option == hint["ai_pick"]


def test_hint_halves_correct_points():
    from backend.core.types import Answer, Outcome
    from backend.scoring import final_score
    out = {"a": Outcome("a", "resolved", "yes", ())}

    def score(hinted: bool) -> int:
        a = Answer("a", "instant", "calm_play", 0, 6, 1, "yes", 1.0, hinted)
        return final_score("u", "m", [a], out, n_typical=1).final_score
    assert score(True) < score(False)


def test_two_unanswered_cards_trigger_one_nudge_until_feed_resume():
    h = started(Harness())
    while not h.msgs("nudge") and h.tick() is not None:
        pass
    assert len(h.msgs("nudge")) == 1
    n = len(h.msgs("question.open"))
    for _ in range(12):
        h.tick()
        if h.room.done:
            break
    assert len(h.msgs("question.open")) == n and len(h.msgs("nudge")) == 1
    h.room.feed_resume("u0")
    for _ in range(12):
        h.tick()
    validate_all(h.log)


def test_player_moments_differ_per_user():
    h = Harness(humans=2)
    h.tick()
    for uid in h.ids:
        for _ in range(3):
            card = h.room.members[uid].open
            if card is not None:
                h.room.answer(uid, card.question.qid, card.question.options[0].id)
    players = {uid: [q for q in h.msgs("question.open", uid) if q["category"] == "player"]
               for uid in h.ids}
    assert players["u0"][0]["player_id"] == "nbr-1-ward"
    assert players["u1"][0]["player_id"] == "cal-9-reyes"
    assert players["u0"][0]["qid"] != players["u1"][0]["qid"]
    forecasts = [[q["qid"] for q in h.msgs("question.open", u) if q["category"] == "forecast"]
                 for u in h.ids]
    assert set(forecasts[0]) & set(forecasts[1])  # shared moments share the qid


def test_moment_without_question_is_skipped_for_everyone():
    from backend.agents.question_generator_stub import propose as stub

    def picky(state, moment, user_player_id, roster=None):
        return None if moment.kind == "instant" else stub(state, moment, user_player_id,
                                                           roster=roster)
    h = Harness(propose=picky)
    h.play()
    assert [q for q in h.msgs("question.open") if q["category"] == "instant"] == []
    assert h.room.done


def calm_then_water_states():
    """lobby, calm 1-29, water break at 30, calm 31-60, full time."""
    states = [st(0.0, mode="lobby", situation="kickoff")]
    states += [st(float(t)) for t in range(1, 30)]
    states.append(st(30.0, mode="break", break_kind="water_break", situation="water_break"))
    states += [st(float(t)) for t in range(31, 60)]
    states.append(st(60.0, mode="full_time", situation="full_time"))
    return states


def test_skip_next_never_jumps_past_a_scheduled_moment():
    h = started(Harness(calm_then_water_states()))
    h.tick()  # t = 1
    assert h.room.control("u0", "skip", "next") is True
    h.tick()
    assert h.room.state.t == 27  # three seconds before the water break at 30
    shown = [q for q in h.msgs("question.open") if q["category"] == "instant"
             and q["open_at"] > 2 and q["lock_at"] <= 27]
    assert shown == []  # moments that locked during the skip are never shown
    h.play(until=lambda: h.room.pause is not None, resume=False, answer=False)
    assert h.room.state.t == 30 and h.room.pause.kind == "water_break"


def test_skip_ignores_calm_spells_and_lands_before_full_time():
    events = {t: (MatchEvent(f"x{t}", float(t), 1, "NBR", "pass", "nbr-8-mensah", (20.0, 34.0),
                             (30.0, 34.0), "complete"),) for t in range(1, 20)}
    states = [st(0.0, mode="lobby", situation="kickoff")]
    states += [st(float(t), new_events=events[t], **{**ATTACK_KW}) for t in range(1, 20)]
    states += [st(float(t), last_attack_end_t=19.0) for t in range(20, 60)]
    states.append(st(60.0, mode="full_time", situation="full_time"))
    h = started(Harness(states))
    h.tick()
    h.room.control("u0", "skip", "next")
    h.tick()
    assert h.room.state.t == 57  # the calm spell at 20 is not a stop; full time at 60 is
    assert "x10" not in [e["id"] for e in h.msgs("match.event")]  # nothing sent while skipping
    assert "x10" in h.room.resolver._seen  # but the resolver was fed
    h.play(answer=False)
    assert h.room.done


ATTACK_KW = dict(mode="attack", situation="attack", threat=0.6, movement=0.9, pressure_m=2.0,
                 ball_xy=(92.0, 30.0), ball_zone="final_third", seconds_since_shot=5.0,
                 attack_active=True)


def test_skip_during_a_pause_acts_like_resume():
    h = open_after_lobby()
    assert h.room.control("u0", "skip", "next") is True
    assert h.room.pause is None


def test_set_piece_card_locks_when_the_kick_is_taken():
    states = [st(0.0, mode="lobby", situation="kickoff")]
    wait = dict(situation="corner", restart_pending=True, ball_zone="final_third",
                ball_xy=(104.0, 0.0), movement=0.1,
                situation_detail={"restart": "corner", "team": "NBR"})
    states += [st(float(t)) for t in range(1, 6)]
    states += [st(float(t), **wait) for t in range(6, 12)]
    states.append(st(12.0, situation="corner", restart_pending=False, restart_taken=True,
                     ball_zone="final_third", ball_xy=(95.0, 30.0), movement=0.8))
    states += [st(float(t), last_attack_end_t=None) for t in range(13, 30)]
    states.append(st(30.0, mode="full_time", situation="full_time"))
    h = Harness(states)
    h.play()
    seq = state_t_at_each_message(h)
    sp = [d for ty, d, _ in seq if ty == "question.open" and d.get("lock_rule") == "on_restart"]
    assert sp, "a set-piece card was shown"
    locked_t = [t for ty, d, t in seq if ty == "question.locked" and d["qid"] == sp[0]["qid"]]
    assert locked_t and locked_t[0] == 12


# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
#
# Review round 1: supervision, host hand-over, deadlines, bots, cost, streak
#
# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%


def run_async(h: Harness) -> None:
    """Drive Room.run to the end; the sleep stands in for time and the host's resume tap."""
    import asyncio

    async def fake_sleep(delay: float) -> None:
        h.clock.t += delay
        if h.room.pause is not None:
            h.room.control(h.host, "resume")
        await asyncio.sleep(0)
    asyncio.run(h.room.run(fake_sleep))


def test_run_skips_a_failing_step_and_keeps_the_room_alive():
    h = Harness()
    real, calls = h.room.tick, {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] in (3, 4, 9):  # never five in a row
            raise RuntimeError("boom")
        return real()
    h.room.tick = flaky
    run_async(h)
    assert h.room.done and len(h.msgs("score.final")) == 1


def test_run_ends_the_room_after_five_failures_in_a_row():
    h = Harness()
    calls = {"n": 0}

    def broken():
        calls["n"] += 1
        raise RuntimeError("boom")
    h.room.tick = broken
    run_async(h)
    assert calls["n"] == 5 and h.room.done


def test_scheduled_card_rejects_answers_after_its_real_time_deadline():
    h = open_after_lobby()
    q = h.msgs("question.open")[0]
    h.clock.t += 61  # past the 60 s window, before the next tick locks it
    assert h.room.answer("u0", q["qid"], q["options"][0]["id"]) is False


def test_host_leaving_promotes_the_first_remaining_human():
    h = Harness(humans=2)
    assert h.msgs("session", "u1")[0]["is_host"] is False
    h.room.remove_member("u0")
    sessions = h.msgs("session", "u1")
    assert sessions[-1]["is_host"] is True and h.room.members["u1"].is_host
    h.tick()
    assert h.room.control("u1", "resume") is True


def test_propose_is_only_called_for_moments_a_human_picked_up():
    from backend.agents.question_generator_stub import propose as stub
    asked: list[str] = []

    def spy(state, moment, user_player_id, roster=None):
        asked.append(moment.moment_id)
        return stub(state, moment, user_player_id, roster=roster)
    h = Harness(propose=spy)
    h.play(answer=False)  # the user ignores cards, so many moments pass unseen
    seen = {q["qid"].split("-")[0] + "-" + q["qid"].split("-")[1] for q in h.msgs("question.open")}
    assert set(asked) <= seen


def test_state_cost_is_the_timing_agents_last_cost():
    h = open_after_lobby()
    h.room.control("u0", "resume")
    for _ in range(6):
        h.tick()
        assert h.msgs("match.state")[-1]["cost"] == pytest.approx(h.room.timing.last_cost)
        assert h.room.cost == h.room.timing.last_cost


def test_you_streak_follows_scorings_rule_over_the_cards_resolved_so_far():
    from backend.scoring.scoring import _scored
    h = Harness()
    h.play()
    m = h.room.members["u0"]
    streaks = [r["you"]["streak"] for r in h.msgs("question.result")]
    assert streaks and all(s >= 0 for s in streaks)
    rows = _scored(h.room._answers(m), h.room.outcomes)
    assert h.room._streak(m) == rows[-1]["streak"]
    # once every card is resolved the on-screen rule and best_streak read the same sequence
    assert max(r["streak"] for r in rows) == h.msgs("score.final")[0]["best_streak"]
