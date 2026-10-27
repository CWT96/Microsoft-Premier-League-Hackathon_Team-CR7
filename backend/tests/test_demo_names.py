"""test_demo_names.py — relabel table, fixed pitch frame and clock label for the demo.

Inputs:  none (pure functions)
Outputs: none
"""

from __future__ import annotations

from backend.demo.names import TEAMS, PLAYERS, display_name, fixed_xy, clock_label


def test_scorers_relabelled():
    assert display_name("910109") == "Saka"
    assert display_name("910209") == "João Pedro"
    assert display_name("910105") == "Saliba"


def test_unknown_player_is_blank():
    assert display_name("999") == "" and display_name(None) == ""


def test_teams():
    assert TEAMS["NBR"]["name"] == "Arsenal" and TEAMS["CAL"]["code"] == "CHE"


def test_every_mapped_number_unique_per_team():
    ars = [n for pid, (_, n) in PLAYERS.items() if pid.startswith("9101")]
    che = [n for pid, (_, n) in PLAYERS.items() if pid.startswith("9102")]
    assert len(ars) == len(set(ars)) and len(che) == len(set(che))


def test_fixed_frame():
    assert fixed_xy("NBR", (90.0, 30.0)) == (90.0, 30.0)
    assert fixed_xy("CAL", (90.0, 30.0)) == (15.0, 38.0)
    assert fixed_xy("CAL", None) is None


def test_clock_label():
    assert clock_label(1, 1434.26) == "23:54"
    assert clock_label(1, 2760) == "45+1:00"
    assert clock_label(2, 3613.2) == "60:13"
    assert clock_label(2, 5667.9) == "90+4:27"


def test_clock_label_pads_minutes():
    assert clock_label(1, 300) == "05:00"
