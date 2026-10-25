"""Shared types serialise to contract-valid payloads."""

from backend.core.types import MatchEvent, TimingDecision, TrackingFrame, clock_key
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
