"""replay_fixtures.py — a tiny hand-built match, fake model and message helpers for app tests.

Last Modified: 2026-10-24 23:59, first version
Inputs:  none
Outputs: MatchBundle, FakeClock, Sink and helpers used by test_replay.py and test_app_api.py

Workflow:
  - ``tiny_states`` lays out lobby, calm play, an attack, a water break, half time, a short
    second half and full time as DetectorStates (one per match second)
  - ``Harness`` drives a Room with a fake clock and collects each member's messages

CAUTION: none
"""

from __future__ import annotations

from typing import Any, Callable

from backend.app.replay import MatchBundle, Room
from backend.core.types import (DetectorState, Match, MatchEvent, Player, Roster, Tracking,
                                TrackingFrame)
from backend.tests.fixtures.timing_scenarios import ATTACK, st

N, C = "NBR", "CAL"
ATTACK_START, ATTACK_END = 10, 14
WATER_T, HT_T = 41, 61
SECOND_HALF_T = 2700


def make_roster() -> Roster:
    players = (
        Player("nbr-1-ward", "Ward", N, "GK", 1),
        Player("nbr-5-stone", "Stone", N, "DF", 5),
        Player("nbr-8-mensah", "Mensah", N, "MF", 8),
        Player("nbr-9-okoro", "Okoro", N, "FW", 9),
        Player("cal-1-fox", "Fox", C, "GK", 1),
        Player("cal-9-reyes", "Reyes", C, "FW", 9),
    )
    return Roster(home=N, away=C, home_name="Northbridge", away_name="Calder", players=players)


def pass_event(t: int, half: int = 1) -> MatchEvent:
    return MatchEvent(f"p{half}-{t}", float(t), half, N, "pass", "nbr-8-mensah", (20.0, 34.0),
                      (30.0, 34.0), "complete")


def calm(start: int, end: int, half: int = 1, **kw: Any) -> list[DetectorState]:
    """Calm states for seconds start..end-1, each with one forward pass."""
    return [st(float(t), half, new_events=(pass_event(t, half),), **kw)
            for t in range(start, end)]


def tiny_states(water_break: bool = True, half_time: bool = True) -> list[DetectorState]:
    shot = MatchEvent("s1", 12.0, 1, N, "shot", "nbr-9-okoro", (88.0, 34.0), (105.0, 34.0), "saved")
    save = MatchEvent("s2", 13.0, 1, C, "save", "cal-1-fox", (3.0, 34.0), None, None)
    states = [st(0.0, mode="lobby", situation="kickoff")]
    states += calm(1, ATTACK_START)
    states += [st(float(t), new_events=(shot,) if t == 12 else (save,) if t == 13 else (),
                  **ATTACK) for t in range(ATTACK_START, ATTACK_END)]
    states += calm(ATTACK_END, WATER_T, last_attack_end_t=float(ATTACK_END - 1))
    if water_break:
        states.append(st(float(WATER_T), mode="break", break_kind="water_break",
                         situation="water_break", last_attack_end_t=13.0))
    states += calm(WATER_T + 1, HT_T, last_attack_end_t=13.0)
    if half_time:
        states.append(st(float(HT_T), mode="break", break_kind="half_time",
                         situation="half_time", last_attack_end_t=13.0))
    states += calm(SECOND_HALF_T, SECOND_HALF_T + 20, half=2)
    states.append(st(float(SECOND_HALF_T + 20), half=2, mode="full_time", situation="full_time"))
    return states


def tiny_bundle(states: list[DetectorState] | None = None, n_typical: int = 10) -> MatchBundle:
    frame = TrackingFrame(3.0, 1, (("nbr-8-mensah", N, 50.0, 30.0),), (50.0, 30.0))
    match = Match("tiny", make_roster(), (), Tracking({(1, 3): frame}), 2760.0, 5400.0)
    return MatchBundle(match, states or tiny_states(), FakeModel(), n_typical)


class FakeModel:
    """option_probs/hint stand-in: favours forward/yes."""

    PROBS = {"yes": 0.7, "no": 0.3, "forward": 0.6, "backward": 0.4, "sideways": 0.0}

    def option_probs(self, template_id: str, state: DetectorState, params: dict) -> dict:
        return dict(self.PROBS)

    def hint(self, probs_shown: dict) -> tuple[str, float]:
        pick = max(probs_shown, key=probs_shown.get)
        return pick, probs_shown[pick]


class FakeSource:
    def __init__(self, bundle: MatchBundle) -> None:
        self.bundle = bundle

    def match_ids(self) -> list[str]:
        return ["tiny"]

    def get(self, match_id: str) -> MatchBundle:
        if match_id != "tiny":
            raise KeyError(match_id)
        return self.bundle


class FakeClock:
    def __init__(self) -> None:
        self.t = 1000.0

    def now(self) -> float:
        return self.t


class Harness:
    """A Room on a fake clock plus the messages each human received."""

    def __init__(self, states: list[DetectorState] | None = None, humans: int = 1,
                 propose: Callable | None = None, speed: float = 15.0) -> None:
        self.clock = FakeClock()
        self.finals: dict[str, dict] = {}
        self.room = Room("r-test", tiny_bundle(states), propose=propose, now=self.clock.now,
                         speed=speed, on_final=lambda u, n, p: self.finals.update({u: p}))
        self.logs: dict[str, list[dict]] = {}
        self.ids: list[str] = []
        for i in range(humans):
            uid = f"u{i}"
            self.logs[uid] = []
            pid = ("nbr-1-ward", "cal-9-reyes")[i % 2]
            self.room.add_member(uid, f"Fan{i}", N, pid, self.logs[uid].append)
            self.ids.append(uid)
        self.host = self.ids[0]

    @property
    def log(self) -> list[dict]:
        return self.logs[self.host]

    def msgs(self, type_: str, uid: str | None = None) -> list[dict]:
        return [m["data"] for m in self.logs[uid or self.host] if m["type"] == type_]

    def tick(self) -> float | None:
        delay = self.room.tick()
        if delay is not None:
            self.clock.t += delay
        return delay

    def play(self, until: Callable[[], bool] = lambda: False, answer: bool = True,
             resume: bool = True, max_ticks: int = 20000) -> None:
        """Tick until ``until()`` or the match ends; optionally answer cards and resume pauses."""
        for _ in range(max_ticks):
            if resume and self.room.pause is not None:
                self.room.control(self.host, "resume")
            if self.tick() is None or until():
                return
            if answer:
                for uid in self.ids:
                    card = self.room.members[uid].open
                    if card is not None:
                        self.room.answer(uid, card.question.qid, card.question.options[0].id)
        raise AssertionError("match did not end")
