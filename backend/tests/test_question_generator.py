"""Adapter from timing moments to the bank, and the bank-backed resolver, on the demo match."""

from functools import lru_cache

import pytest

from backend.agents.question_generator import BankGenerator
from backend.core.types import Moment
from backend.detect.detector import Detector
from backend.ingest import load_match
from backend.resolve import Resolver


@lru_cache(maxsize=1)
def _match_and_states():
    m = load_match("nbr-cal-seed42")
    return m, list(Detector(m.roster, m.roster.home, m.roster.away).run(m))


def _state_at(t: float, half: int = 1):
    _, states = _match_and_states()
    return next(s for s in states if s.half == half and s.t >= t and s.mode != "lobby")


def _moment(mid: str, kind: str, category: str, open_at: float, lock_at: float,
            half: int = 1) -> Moment:
    return Moment(mid, kind, category, open_at, lock_at, "fixed", "calm_play", 0.2, half)


def _gen() -> BankGenerator:
    return BankGenerator(_match_and_states()[0])


def _first_filled(gen: BankGenerator, pid: str | None):
    for t in range(300, 2400, 37):
        s = _state_at(t)
        q = gen.propose(s, _moment(f"m{t}", "instant", "instant", s.t, s.t + 6), pid)
        if q is not None:
            return s, q
    pytest.fail("no slot filled in the first half")


def test_two_rooms_get_the_same_question_at_the_same_second() -> None:
    _, q1 = _first_filled(_gen(), None)
    _, q2 = _first_filled(_gen(), None)
    assert q1 == q2


def test_question_has_two_labelled_options_and_bank_probabilities() -> None:
    gen = _gen()
    _, q = _first_filled(gen, None)
    assert len(q.options) == 2 and all(o.label for o in q.options)
    p = gen.probs(q.qid)
    assert set(p) == {o.id for o in q.options} and sum(p.values()) == pytest.approx(1.0)


def test_kickoff_stack_gives_two_different_forecasts_and_a_player_card() -> None:
    m, _ = _match_and_states()
    gen, s = _gen(), _state_at(0)
    pid = next(p.player_id for p in m.roster.players if p.position == "MF" and p.starter)
    qs = [gen.propose(s, _moment(f"k{i}", "kickoff_stack", cat, 0.0, 0.0), pid)
          for i, cat in enumerate(("forecast", "forecast", "player"))]
    assert all(qs)
    assert qs[0].template_id != qs[1].template_id
    assert qs[2].category == "player" and qs[2].player_id == pid and qs[2].qid == f"k2-{pid}"


def test_resolver_settles_a_watched_question_from_later_events() -> None:
    m, states = _match_and_states()
    gen = _gen()
    s, q = _first_filled(gen, None)
    res = Resolver(gen)
    res.watch(q, q.lock_at, s.half)
    out = []
    for st in states:
        if (st.half, st.t) > (s.half, s.t):
            out += res.step(st.half, st.t, st.new_events)
    out += res.on_full_end()
    assert [o.qid for o in out] == [q.qid]
    assert out[0].status == "void" or out[0].correct_option in {o.id for o in q.options}


def test_resolver_refuses_a_question_the_bank_did_not_make() -> None:
    gen = _gen()
    _, q = _first_filled(gen, None)
    with pytest.raises(ValueError):
        Resolver(_gen()).watch(q, q.lock_at, 1)


NEXT_PASS = {"next_pass_dir", "next_pass_complete", "next_pass_long"}


def test_unsafe_set_on_current_data_is_the_three_next_pass_templates() -> None:
    from backend.agents.quiet import open_play_settle_rates, unsafe_in_open_play
    assert unsafe_in_open_play() == NEXT_PASS
    rates = open_play_settle_rates()
    assert all(rates[t] < 0.5 for t in ("player_next_pass_forward", "player_next_pass_long"))


def _templates_by_situation(situation: str) -> set[str]:
    gen, seen = _gen(), set()
    for t in range(200, 2600, 11):
        s = _state_at(t)
        m = Moment(f"m{t}", "instant", "instant", s.t, s.t + 6, "fixed", situation, 0.2, 1)
        q = gen.propose(s, m, None)
        if q is not None:
            seen.add(q.template_id)
    return seen


def test_open_play_never_asks_a_next_pass_question_but_a_dead_ball_can() -> None:
    assert not (_templates_by_situation("calm_play") & NEXT_PASS)
    assert _templates_by_situation("throw_in") & NEXT_PASS
