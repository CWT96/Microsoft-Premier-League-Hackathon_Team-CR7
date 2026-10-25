"""Shared types serialise to contract-valid payloads."""

import pytest

from backend.core.types import (MatchEvent, Moment, Player, Roster, TimingDecision, TrackingFrame,
                                clock_key, player_qid)
from backend.tests.contract_utils import validate


def test_event_to_contract_drops_absent_coordinates() -> None:
    ev = MatchEvent(id="e1", t=10, half=1, team="NBR", type="kickoff")
    payload = ev.to_contract()
    assert "start" not in payload and "end" not in payload
    validate("event", payload)


def test_event_with_coordinates_validates() -> None:
    ev = MatchEvent(id="e2", t=2066, half=1, team="NBR", type="pass", player_id="nbr-8",
                    start=(52, 30), end=(68, 22), outcome="complete")
    validate("event", ev.to_contract())


def test_substitution_event_validates() -> None:
    ev = MatchEvent(id="e3", t=3900, half=2, team="CAL", type="substitution",
                    player_id="cal-9", related_player_id="cal-14")
    validate("event", ev.to_contract())


def test_tracking_frame_validates() -> None:
    frame = TrackingFrame(t=5, half=1, players=(("nbr-1", "NBR", 5.0, 34.0),), ball=(52.5, 34.0))
    validate("tracking", frame.to_contract())


def test_tracking_frame_without_ball_validates() -> None:
    validate("tracking", TrackingFrame(t=5, half=1, players=(), ball=None).to_contract())


def test_timing_decision_validates() -> None:
    d = TimingDecision(t=100, half=1, action="open", source="rule", reason="calm",
                       inputs={"cost": 0.2, "calm_for_s": 4}, moment_ids=("q-1",),
                       situation="calm_play")
    validate("timing_decision", d.to_contract())


def test_clock_key_orders_second_half_after_first_half_stoppage() -> None:
    assert clock_key(1, 2760) < clock_key(2, 2700)


def test_moment_is_hashable_despite_dict_field() -> None:
    m = Moment(moment_id="q-1", kind="instant", category="instant", open_at=1, lock_at=7,
               lock_rule="fixed", situation="calm_play", cost=0.2, half=1,
               situation_detail={"team": "NBR"})
    assert m in {m}


def test_unknown_event_type_rejected() -> None:
    with pytest.raises(ValueError):
        MatchEvent(id="x", t=1, half=1, team="NBR", type="dance")


def test_timing_decision_requires_cost() -> None:
    with pytest.raises(ValueError):
        TimingDecision(t=1, half=1, action="open", source="rule", reason="r", inputs={})


def test_roster_lookup_and_opponent() -> None:
    r = Roster(home="NBR", away="CAL", home_name="Northbridge FC", away_name="Calder Town",
               players=(Player("nbr-1", "Hale", "NBR", "GK", 1),))
    assert r.by_id()["nbr-1"].name == "Hale"
    assert r.opponent("NBR") == "CAL"
    with pytest.raises(ValueError):
        r.opponent("XYZ")


def test_player_qid() -> None:
    assert player_qid("q-40", "cal-1") == "q-40-cal-1"
