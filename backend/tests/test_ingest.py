"""Hudl/StatsBomb ingest: ``load_match`` on the committed dataset, plus the data/README must-haves.

Dataset realism (rates per position, spell counts) is tested by ``data/generator/tests``; these
tests cover what ingest adds: the contract mapping, restarts, the clock and the roster.
"""

import json
from collections import Counter
from pathlib import Path

import jsonschema
import pytest

from backend.core.constants import (DEFAULT_MATCH_ID, PITCH_LENGTH_M, PITCH_WIDTH_M,
                                    RESTART_TYPES, SECOND_HALF_START_S)
from backend.core.types import Match
from backend.ingest import load_match
from backend.ingest.loader import EVENT_SCHEMA_PATH, SOURCE_DIR, list_match_ids
from backend.ingest.statsbomb import convert_events, read_match

SCORES = {"nbr-cal-seed42": {"NBR": 2, "CAL": 1}, "har-wex-seed7": {"HAR": 1},
          "wex-cal-seed2026": {"WEX": 3, "CAL": 3}}
ON_BALL = {"pass", "carry", "shot"}


@pytest.fixture(scope="module", params=sorted(SCORES))
def match(request) -> Match:
    return load_match(request.param)


def test_lists_the_three_matches_and_default_is_one_of_them() -> None:
    assert list_match_ids() == ["nbr-cal-seed42", "har-wex-seed7", "wex-cal-seed2026"]
    assert DEFAULT_MATCH_ID in list_match_ids()


def test_slug_and_hudl_id_load_the_same_match() -> None:
    assert load_match("9000001").events == load_match("nbr-cal-seed42").events


def test_every_event_validates_against_the_contract() -> None:
    validator = jsonschema.Draft202012Validator(json.loads(EVENT_SCHEMA_PATH.read_text()))
    _, raw, _ = read_match(DEFAULT_MATCH_ID, SOURCE_DIR)
    assert not [e["id"] for e in raw if list(validator.iter_errors(e))]


def test_scores_match_the_dataset_note(match: Match) -> None:
    assert dict(Counter(e.team for e in match.events if e.type == "goal")) == \
        SCORES[match.match_id]


def test_clock_second_half_starts_at_2700_and_events_are_sorted(match: Match) -> None:
    keys = [(e.half, e.t, e.id) for e in match.events]
    assert keys == sorted(keys)
    assert min(e.t for e in match.events if e.half == 2) >= SECOND_HALF_START_S
    assert match.first_half_end_t >= 2700 and match.full_end_t >= 5400


def test_must_have_1_coordinates_on_every_on_ball_event_inside_the_pitch(match: Match) -> None:
    for e in match.events:
        if e.type in ON_BALL:
            assert e.start and e.end, e.id
            for x, y in (e.start, e.end):
                assert 0 <= x <= PITCH_LENGTH_M and 0 <= y <= PITCH_WIDTH_M


def test_must_have_2_roster_positions(match: Match) -> None:
    starters = [p for p in match.roster.players if p.starter]
    assert len(starters) == 22
    per_team = Counter((p.team, p.position) for p in starters)
    for team in (match.roster.home, match.roster.away):
        assert per_team[(team, "GK")] == 1 and per_team[(team, "DF")] >= 3


def test_restart_is_awarded_before_the_kick_by_the_same_team(match: Match) -> None:
    events = list(match.events)
    awards = [i for i, e in enumerate(events) if e.type in RESTART_TYPES]
    assert awards
    for i in awards:
        award = events[i]
        kick = next(e for e in events[i + 1:] if e.type in ("pass", "shot"))
        assert kick.team == award.team and kick.t >= award.t


def test_throw_ins_give_a_dead_ball_wait_of_several_seconds(match: Match) -> None:
    events = list(match.events)
    waits = [next(k for k in events[i + 1:] if k.type == "pass").t - e.t
             for i, e in enumerate(events) if e.type == "throw_in"]
    assert sorted(waits)[len(waits) // 2] > 5


def test_cards_and_substitutions_are_kept() -> None:
    m = load_match("wex-cal-seed2026")
    assert {e.outcome for e in m.events if e.type == "card"} == {"yellow", "red"}
    subs = [e for e in m.events if e.type == "substitution"]
    assert subs and all(e.related_player_id for e in subs)


def test_unknown_match_raises() -> None:
    with pytest.raises(FileNotFoundError):
        load_match("no-such-match")


def test_unknown_team_id_raises() -> None:
    raw = [{"index": 1, "period": 1, "timestamp": "00:00:01.000", "type": {"name": "Pass"},
            "team": {"id": 1}, "pass": {}}]
    with pytest.raises(KeyError):
        convert_events(raw, {})


def test_no_placeholder_left_in_the_dataset_folder() -> None:
    assert not (SOURCE_DIR / "sim-placeholder").exists()
    assert not list(Path(SOURCE_DIR).glob("*/roster.json"))
