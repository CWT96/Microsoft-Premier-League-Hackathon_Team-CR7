import json

from backend.demo.script import build_timeline
from backend.demo.shape import load_slots, on_pitch, team_shape
from backend.ingest.loader import load_match

TL = build_timeline(load_match("nbr-cal-seed42"))
SHAPES = [c for c in TL.cues if c.type == "demo.shape"]


def test_every_shape_has_eleven_a_side():
    assert SHAPES
    for c in SHAPES:
        teams = [p["team"] for p in c.data["players"]]
        assert teams.count("ARS") == 11 and teams.count("CHE") == 11


def test_all_inside_pitch():
    for c in SHAPES:
        assert all(1 <= p["x"] <= 104 and 1 <= p["y"] <= 67 for p in c.data["players"])


def test_holder_is_on_the_ball():
    # a shape is where everyone is at the next beat: the one before Saka's shot has him on the ball
    events = [c for c in TL.cues if c.type == "demo.event" and c.data["xy"]]
    saka = next(e for e in events if e.data["player"] == "Saka" and e.data["type"] == "shot")
    before = [c for c in SHAPES if c.at < saka.at][-1]
    dot = next(p for p in before.data["players"] if p["num"] == 7 and p["team"] == "ARS")
    assert [dot["x"], dot["y"]] == saka.data["xy"] == before.data["ball"]
    assert before.data["holder"] == "Saka"


def test_goalkeepers_stay_home():
    for c in SHAPES:
        gks = {p["team"]: p for p in c.data["players"] if p["num"] == 1}
        assert gks["ARS"]["x"] < 20 and gks["CHE"]["x"] > 85


def test_substitute_replaces_player():
    slots = load_slots()
    before = {s.player_id for s in on_pitch(slots, 2, 3400.0)}
    after = {s.player_id for s in on_pitch(slots, 2, 3420.0)}
    assert "910107" in before and "910107" not in after
    assert len(after) == 22


def test_calls_and_duration_unchanged():
    assert len(TL.calls) == 6 and TL.duration == 99.0 and TL.cues[-1].type == "demo.full_time"


def test_shape_payload_small():
    size = len(json.dumps([c.data for c in TL.cues]))
    assert size < 60_000


def test_team_shape_rounds_and_returns_players():
    out = team_shape(on_pitch(load_slots(), 1, 0.0), (52.5, 34.0), None)
    assert len(out) == 22 and all(round(p["x"], 1) == p["x"] for p in out)
