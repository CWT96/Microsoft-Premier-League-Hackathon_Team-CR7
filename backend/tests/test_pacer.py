"""test_pacer.py — per-user pacer: pick-up rules, fast vs slow users, nudge, join any time.

Last Modified: 2026-10-24 23:05, first version
Inputs:  none
Outputs: none

Workflow:
  - simulate users against a shared moment stream, one tick per match second

CAUTION: none
"""

from __future__ import annotations

from backend.agents import timing_rules as R
from backend.app.pacer import Pacer
from backend.core.types import Moment


def mom(i: int, open_at: float, kind="instant", category="instant", window=R.CARD_WINDOW_S,
        half=1) -> Moment:
    return Moment(moment_id=f"q-{i}", kind=kind, category=category, open_at=open_at,
                  lock_at=open_at + window, lock_rule="fixed", situation="calm_play", cost=0.1,
                  half=half)


def stream(n: int, every: float = 4.0, start: float = 100.0) -> list[Moment]:
    return [mom(i + 1, start + i * every) for i in range(n)]


def open_at(moments, t):
    return [m for m in moments if m.open_at <= t < m.lock_at]


def simulate(answer_s: float, cycle_s: float, moments, end: float, answer=True) -> list[str]:
    """User answers ``answer_s`` after pickup and is free again ``cycle_s`` after pickup."""
    pacer, seen = Pacer("u"), []
    free_at, card, t = 0.0, None, moments[0].open_at
    while t < end:
        if card and answer and t >= card[1] + answer_s:
            pacer.on_answer(card[0].moment_id, t)
            card = None
        if card and t >= card[0].lock_at:
            pacer.on_lock(card[0].moment_id, answered=False)
            card = None
        if card is None and t >= free_at:
            m = pacer.next_card(t, 1, open_at(moments, t))
            if m:
                seen.append(m.moment_id)
                card, free_at = (m, t), t + cycle_s
        t += 1.0
    return seen


def test_fast_user_sees_about_twice_as_many_as_slow_user():
    ms = stream(40)
    fast = simulate(1.0, 2.0, ms, 260)
    slow = simulate(4.0, 8.0, ms, 260)
    assert len(fast) >= 1.8 * len(slow)
    assert len(set(fast)) == len(fast) and len(set(slow)) == len(slow)  # never re-delivered


def test_takes_newest_open_moment_with_enough_time_left():
    p = Pacer("u")
    ms = [mom(1, 100), mom(2, 103)]
    assert p.next_card(104, 1, ms).moment_id == "q-2"


def test_never_delivers_with_less_than_the_margin_left():
    p = Pacer("u")
    m = mom(1, 100)  # locks at 108, margin 5 s
    assert p.next_card(103.5, 1, [m]) is None
    assert p.next_card(103.0, 1, [m]).moment_id == "q-1"
    p2 = Pacer("v")
    assert p2.next_card(109, 1, [m]) is None  # already locked


def test_never_redelivers_and_blocks_while_card_is_open():
    p = Pacer("u")
    a, b = mom(1, 100), mom(2, 104)
    assert p.next_card(100, 1, [a]).moment_id == "q-1"
    assert p.next_card(104, 1, [a, b]) is None  # card q-1 still open for this user
    p.on_answer("q-1", 101)
    assert p.next_card(104, 1, [a, b]).moment_id == "q-2"
    p.on_answer("q-2", 105)
    assert p.next_card(105, 1, [a, b]) is None  # both already seen


def test_nudge_after_two_unanswered_locks_and_no_cards_until_resume():
    ms = stream(10)
    p = Pacer("u")
    for i, m in enumerate(ms[:2]):
        assert p.next_card(m.open_at, 1, [m]).moment_id == m.moment_id
        p.on_lock(m.moment_id, answered=False)
        assert (p.pending_nudge() is None) == (i == 0)
    p.next_card(ms[2].open_at, 1, [ms[2]])  # paused: nothing
    assert p.pending_nudge() == R.NUDGE_TEXT  # repeated until resume
    assert p.next_card(ms[3].open_at, 1, [ms[3]]) is None
    p.resume()
    assert p.next_card(ms[4].open_at, 1, [ms[4]]).moment_id == ms[4].moment_id


def test_nudge_text_fits_contract():
    p = Pacer("u")
    for i in (1, 2):
        p.next_card(100 * i, 1, [mom(i, 100 * i)])
        p.on_lock(f"q-{i}", answered=False)
    text = p.pending_nudge()
    assert text == R.NUDGE_TEXT and len(text) <= 80


def test_answer_resets_the_unanswered_streak():
    p = Pacer("u")
    for i, ans in ((1, False), (2, True), (3, False)):
        p.next_card(100 * i, 1, [mom(i, 100 * i)])
        if ans:
            p.on_answer(f"q-{i}", 100 * i + 1)
        else:
            p.on_lock(f"q-{i}", answered=False)
    assert p.pending_nudge() is None
    assert p.next_card(500, 1, [mom(9, 500)]) is not None


def test_join_at_fifty_minutes_gets_next_moment_immediately():
    p = Pacer("late")
    ms = [mom(30, 3000, half=1), mom(31, 3004, half=1)]
    assert p.next_card(3005, 1, ms).moment_id == "q-31"


def test_scheduled_stack_delivered_in_order_and_not_counted_as_ignored():
    stack = [mom(i, 0, kind="kickoff_stack", category=c, window=60.0)
             for i, c in ((1, "forecast"), (2, "forecast"), (3, "player"))]
    p = Pacer("u")
    got = []
    for m in stack:
        got.append(p.next_card(0, 1, stack).moment_id)
        p.on_lock(got[-1], answered=False)
    assert got == ["q-1", "q-2", "q-3"]
    assert p.pending_nudge() is None
    assert p.next_card(10, 1, [mom(4, 10)]) is not None


def test_player_instant_takes_priority_over_newer_shared_moment():
    p = Pacer("u")
    player = mom(1, 100, kind="player_instant", category="player")
    shared = mom(2, 102)
    assert p.next_card(103, 1, [player, shared]).moment_id == "q-1"


def test_other_half_moments_are_ignored():
    p = Pacer("u")
    assert p.next_card(2800, 2, [mom(1, 2700, half=1)]) is None


# .............................................................................
# review fixes
# .............................................................................


def test_open_card_past_lock_counts_as_unanswered_lock():
    p2 = Pacer("v")
    for i in (1, 2):
        p2.next_card(100 * i, 1, [mom(i, 100 * i)])
        p2.next_card(100 * i + R.CARD_WINDOW_S, 1, [])  # app never called on_lock
    assert p2.paused and p2.pending_nudge() == R.NUDGE_TEXT


def test_lock_now_closes_card_without_counting_and_blocks_redelivery():
    p = Pacer("u")
    m = mom(1, 100)
    p.next_card(100, 1, [m])
    p.lock_now(["q-1"])
    assert not p.paused
    assert p.next_card(101, 1, [m]) is None
    assert p.next_card(101, 1, [mom(2, 100)]).moment_id == "q-2"


def test_locked_ids_argument_hides_moments():
    p = Pacer("u")
    a, b = mom(1, 100), mom(2, 101)
    assert p.next_card(102, 1, [a, b], locked_ids=["q-2"]).moment_id == "q-1"


def test_nudge_repeats_until_resume():
    p = Pacer("u")
    for i in (1, 2):
        p.next_card(100 * i, 1, [mom(i, 100 * i)])
        p.on_lock(f"q-{i}", answered=False)
    assert p.pending_nudge() == p.pending_nudge() == R.NUDGE_TEXT
    p.resume()
    assert p.pending_nudge() is None


def test_resume_without_nudge_is_harmless():
    p = Pacer("u")
    p.resume()
    assert p.next_card(100, 1, [mom(1, 100)]) is not None


def test_on_lock_answered_true_resets_streak():
    p = Pacer("u")
    for i, ans in ((1, False), (2, True), (3, False)):
        p.next_card(100 * i, 1, [mom(i, 100 * i)])
        p.on_lock(f"q-{i}", answered=ans)
    assert not p.paused and p.pending_nudge() is None


def test_scheduled_stack_ignores_match_clock_until_locked_by_app():
    stack = [mom(i, 2760, kind="ht_stack", category="forecast", window=60.0, half=2)
             for i in (1, 2)]
    p = Pacer("u")
    # match clock restarted at 2700 in half 2 after resume; stack still deliverable
    assert p.next_card(2700, 1, stack).moment_id == "q-1"
    p.on_lock("q-1", answered=False)
    assert p.next_card(5000, 2, stack).moment_id == "q-2"
    p.lock_now(["q-2"])
    assert p.next_card(5000, 2, stack) is None


def test_second_half_join():
    p = Pacer("late")
    assert p.next_card(4000, 2, [mom(80, 3998, half=2)]).moment_id == "q-80"


def test_fast_user_is_bounded_by_the_stream_and_slow_user_still_served():
    ms = stream(40)
    fast = simulate(0.0, 1.0, ms, 260)
    slow = simulate(5.0, 10.0, ms, 260)
    assert len(fast) <= len(ms) and len(slow) >= 5


def test_fractional_margin():
    p = Pacer("u")
    m = mom(1, 100)
    assert p.next_card(102.99, 1, [m]) is not None
    assert Pacer("v").next_card(103.01, 1, [m]) is None


def test_mismatched_qid_is_logged(caplog):
    p = Pacer("u")
    p.next_card(100, 1, [mom(1, 100)])
    with caplog.at_level("WARNING"):
        p.on_lock("q-9", answered=False)
        p.on_answer("q-9", 101)
    assert len(caplog.records) == 2


def test_lock_now_on_other_moment_prevents_later_delivery():
    p = Pacer("u")
    p.next_card(100, 1, [mom(1, 100)])
    p.lock_now(["q-9"])  # not this user's open card
    p.on_answer("q-1", 101)
    other = mom(9, 101)
    assert p.next_card(102, 1, [other]) is None
