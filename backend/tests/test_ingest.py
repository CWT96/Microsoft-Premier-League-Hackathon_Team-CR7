"""Placeholder dataset, generator determinism and ``load_match``; also data/README.md must-haves."""

from __future__ import annotations

import json
import math
from collections import Counter
from pathlib import Path

import pytest

from backend.core.constants import DEFAULT_MATCH_ID, OWN_THIRD_MAX_X
from backend.core.types import Match
from backend.ingest import load_match
from backend.ingest.loader import SOURCE_DIR
from backend.ingest.sim_placeholder import DEFAULT_OUT_DIR, generate
from backend.tests.contract_utils import validate

ON_BALL = {"pass", "carry", "shot", "save", "clearance", "tackle", "interception", "foul", "out",
           "goal", "throw_in", "goal_kick", "corner", "free_kick", "penalty"}
NEEDS_END = {"pass", "carry", "shot", "clearance"}
LONG_CALM_S = 90.0
ENDING_WINDOW_S = 3.0  # an attack's closing shot, save, foul or out lies this close to its end
CALM_MAX_SPEED, ATTACK_MIN_SPEED = 1.6, 3.0  # mean player speed, m/s


@pytest.fixture(scope="module")
def match() -> Match:
    return load_match()


@pytest.fixture(scope="module")
def raw_events() -> list[dict]:
    lines = (DEFAULT_OUT_DIR / "events.jsonl").read_text().splitlines()
    return [json.loads(line) for line in lines]


@pytest.fixture(scope="module")
def labels() -> list[dict]:
    return json.loads((DEFAULT_OUT_DIR / "labels.json").read_text())


def of_kind(labels: list[dict], kind: str) -> list[dict]:
    return [lb for lb in labels if lb["kind"] == kind]


def mean_speed(match: Match, half: int, t0: float, t1: float) -> float:
    """Mean player speed (m/s) over [t0, t1], all players, 1 s frames."""
    assert match.tracking is not None
    speeds = []
    for s in range(int(t0) + 1, int(t1) + 1):
        a, b = match.tracking.at(half, s - 1), match.tracking.at(half, s)
        prev = {pid: (x, y) for pid, _, x, y in a.players}
        speeds += [math.dist(prev[pid], (x, y)) for pid, _, x, y in b.players if pid in prev]
    return sum(speeds) / len(speeds)


# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
# Contract and structure
# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%


def test_every_event_validates(raw_events: list[dict]) -> None:
    for ev in raw_events:
        validate("event", ev)


def test_ids_are_stable_and_events_sorted(match: Match, raw_events: list[dict]) -> None:
    assert [e["id"] for e in raw_events] == [f"e{i:04d}" for i in range(1, len(raw_events) + 1)]
    keys = [(e.half, e.t) for e in match.events]
    assert keys == sorted(keys)
    assert [e.id for e in match.events] == [e["id"] for e in raw_events]


def test_match_markers_and_clock(match: Match) -> None:
    types = Counter(e.type for e in match.events)
    assert types["kickoff"] >= 2 and types["half_end"] == 1 and types["full_end"] == 1
    assert match.match_id == DEFAULT_MATCH_ID
    assert match.first_half_end_t > 2700 and match.full_end_t > 5400
    second_kickoff = [e for e in match.events if e.type == "kickoff" and e.half == 2][0]
    assert second_kickoff.t == 2700
    assert all(e.half == 1 for e in match.events if e.t < 2700 and e.half == 1)


def test_roster_shape(match: Match) -> None:
    for team in (match.roster.home, match.roster.away):
        players = match.roster.team_players(team)
        starters = [p for p in players if p.starter]
        assert Counter(p.position for p in starters) == {"GK": 1, "DF": 4, "MF": 4, "FW": 2}
        assert len(players) - len(starters) == 3
    names = {p.name for p in match.roster.players}
    assert {"Ward", "Okafor", "Mensah", "Doyle", "Hale", "Duarte"} <= names
    assert "nbr-8-okafor" in match.roster.by_id()


# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
# data/README.md must-have checks
# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%


def test_must_have_1_ball_coordinates_on_every_on_ball_event(match: Match) -> None:
    on_ball = [e for e in match.events if e.type in ON_BALL]
    assert len(on_ball) > 1000
    assert all(e.start is not None for e in on_ball)
    assert all(e.end is not None for e in on_ball if e.type in NEEDS_END)


def test_must_have_2_positions_and_position_fitting_stats(match: Match) -> None:
    by_id = match.roster.by_id()
    assert {p.position for p in match.roster.players} == {"GK", "DF", "MF", "FW"}

    def count(typ: str, **kw: str) -> Counter:
        return Counter(by_id[e.player_id].position for e in match.events
                       if e.type == typ and e.player_id and all(getattr(e, k) == v
                                                                for k, v in kw.items()))
    passes, shots, clears = count("pass"), count("shot"), count("clearance")
    assert passes.most_common(1)[0][0] == "MF"
    assert shots.most_common(1)[0][0] == "FW"
    assert clears.most_common(1)[0][0] == "DF"
    saves = Counter(e.player_id for e in match.events if e.type == "save")
    assert all(by_id[pid].position == "GK" for pid in saves)
    assert len(saves) == 2 and all(2 <= n <= 5 for n in saves.values())


def test_must_have_3_calm_spells_and_attacks(match: Match, labels: list[dict]) -> None:
    calm, attacks = of_kind(labels, "calm"), of_kind(labels, "attack")
    assert len(calm) >= 12 and len(attacks) >= 15
    assert sum(lb["t_end"] - lb["t_start"] >= LONG_CALM_S for lb in calm) >= 3
    # a calm spell is passing only, and a back-third spell stays in the own third
    back = 0
    for lb in calm:
        evs = [e for e in match.events if e.half == lb["half"]
               and lb["t_start"] <= e.t <= lb["t_end"] and e.type in ("pass", "carry")]
        assert len(evs) >= 4
        back += all(e.start[0] <= OWN_THIRD_MAX_X + 1 for e in evs)
    assert back >= 3
    ends = Counter(e.type for lb in attacks for e in match.events
                   if e.half == lb["half"] and lb["t_end"] - ENDING_WINDOW_S <= e.t <= lb["t_end"])
    assert {"shot", "save", "foul", "out"} <= set(ends)


# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
# Required content
# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%


def test_goals_cards_and_set_pieces(match: Match) -> None:
    goals = [e for e in match.events if e.type == "goal"]
    assert Counter(e.team for e in goals) == {"NBR": 2, "CAL": 1}
    types = Counter(e.type for e in match.events)
    assert types["corner"] >= 1 and types["free_kick"] >= 1 and types["penalty"] == 1
    assert types["var_check"] == 1 and types["injury"] == 1
    cards = [e for e in match.events if e.type == "card"]
    assert any(c.half == 2 for c in cards) and any(c.half == 1 for c in cards)
    assert types["throw_in"] >= 3 and types["goal_kick"] >= 3 and types["foul"] >= 3


def test_substitutions_three_per_team_in_second_half(match: Match) -> None:
    subs = [e for e in match.events if e.type == "substitution"]
    assert Counter(e.team for e in subs) == {"NBR": 3, "CAL": 3}
    assert all(e.half == 2 and e.related_player_id for e in subs)


def test_waits_and_stoppages(labels: list[dict]) -> None:
    waits = {lb["note"].split(" ", 1)[1]: lb["t_end"] - lb["t_start"]
             for lb in of_kind(labels, "set_piece_wait")}
    assert all(abs(d - 20) <= 3 for k, d in waits.items() if k == "corner")
    assert any(abs(d - 25) <= 3 for k, d in waits.items() if k.startswith("free kick"))
    assert any(abs(d - 60) <= 4 for k, d in waits.items() if k == "penalty")
    water = of_kind(labels, "water_break")
    assert [lb["half"] for lb in water] == [1, 2]
    assert all(abs((lb["t_end"] - lb["t_start"]) - 90) < 1 for lb in water)
    assert abs(water[0]["t_start"] - 22 * 60) <= 30 and abs(water[1]["t_start"] - 67 * 60) <= 30
    injury = [lb for lb in of_kind(labels, "stoppage") if "injury" in lb["note"]]
    assert len(injury) == 1 and 55 <= injury[0]["t_end"] - injury[0]["t_start"] <= 65


def test_calm_spells_at_the_timing_scenario_moments(labels: list[dict]) -> None:
    calm = of_kind(labels, "calm")

    def covers(t: float) -> bool:
        return any(lb["t_start"] <= t <= lb["t_end"] for lb in calm)
    assert covers(2400) and covers(4200) and covers(2690)


def test_an_attack_starts_within_5s_after_a_calm_spell(labels: list[dict]) -> None:
    calm, attacks = of_kind(labels, "calm"), of_kind(labels, "attack")
    assert any(0 <= a["t_start"] - c["t_end"] <= 5 and a["half"] == c["half"]
               for a in attacks for c in calm)


COUNT_RANGES = {"throw_in": (35, 45), "goal_kick": (12, 18), "foul": (18, 26), "corner": (8, 12)}
STALLED_RANGE, STALLED_MIN_S, STALLED_MAX_SPEED = (4, 6), 10.0, 1.6
RESTART_TAKER_TYPES = {"pass", "carry", "shot"}
MIN_LONG_CALM = 12


def test_realistic_restart_and_foul_counts(match: Match) -> None:
    types = Counter(e.type for e in match.events)
    for typ, (lo, hi) in COUNT_RANGES.items():
        assert lo <= types[typ] <= hi, (typ, types[typ])
    assert types["free_kick"] >= types["foul"] - types["penalty"] - 2  # a foul gives a free kick
    assert sum(lb["t_end"] - lb["t_start"] >= LONG_CALM_S for lb in of_kind_all("calm")) \
        >= MIN_LONG_CALM


def of_kind_all(kind: str) -> list[dict]:
    return of_kind(json.loads((DEFAULT_OUT_DIR / "labels.json").read_text()), kind)


def test_every_restart_award_is_followed_by_the_awarded_teams_kick(match: Match) -> None:
    evs = list(match.events)
    checked = 0
    for i, e in enumerate(evs):
        if e.type in ("throw_in", "goal_kick", "free_kick"):
            nxt = evs[i + 1]
            assert nxt.team == e.team and nxt.type in RESTART_TAKER_TYPES and nxt.player_id, e.id
            assert 3 <= nxt.t - e.t <= 30
            checked += 1
    assert checked >= 60


def test_throw_ins_follow_an_out_by_the_other_team_with_a_short_wait(match: Match) -> None:
    evs = list(match.events)
    for i, e in enumerate(evs):
        if e.type == "throw_in":
            assert evs[i - 1].type == "out" and evs[i - 1].team != e.team
            assert 5 <= evs[i + 1].t - e.t <= 10.1
        if e.type == "goal_kick":
            assert evs[i - 1].type == "out" and evs[i - 1].team != e.team
            assert 8 <= evs[i + 1].t - e.t <= 15.1


def test_stalled_spells_near_the_box(match: Match, labels: list[dict]) -> None:
    stalled = of_kind(labels, "stalled")
    assert STALLED_RANGE[0] <= len(stalled) <= STALLED_RANGE[1]
    for lb in stalled:
        assert lb["t_end"] - lb["t_start"] >= STALLED_MIN_S
        evs = [e for e in match.events if e.half == lb["half"]
               and lb["t_start"] <= e.t <= lb["t_end"]]
        passes = [e for e in evs if e.type == "pass"]  # the closing tackle shares the end second
        assert len(passes) >= 3 and all(69 <= e.start[0] <= 86 for e in passes)
        assert not any(e.type == "shot" for e in evs)
        assert mean_speed(match, lb["half"], lb["t_start"], lb["t_end"]) < STALLED_MAX_SPEED


def test_movement_is_low_while_waiting_for_a_restart(match: Match) -> None:
    evs = list(match.events)
    for i, e in enumerate(evs):
        if e.type in ("throw_in", "goal_kick") and evs[i + 1].t - e.t >= 5:
            assert mean_speed(match, e.half, e.t, evs[i + 1].t) < CALM_MAX_SPEED


# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
# Tracking
# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%


def test_tracking_covers_every_second(match: Match) -> None:
    assert match.tracking is not None
    spans = ((1, 0, int(match.first_half_end_t)), (2, 2700, int(match.full_end_t)))
    for half, first, last in spans:
        for s in range(first, last + 1):
            frame = match.tracking.at(half, s)
            assert frame is not None and len(frame.players) == 22 and frame.ball is not None
    for s in range(0, int(match.full_end_t), 97):
        half = 1 if s < 2700 else 2
        frame = match.tracking.at(half, s)
        if frame:
            validate("tracking", frame.to_contract())


def test_tracking_uses_the_fixed_frame(match: Match) -> None:
    assert match.tracking is not None
    for half, t in ((1, 600), (2, 3000)):
        frame = match.tracking.at(half, t)
        gk = {pid: x for pid, tm, x, _ in frame.players if pid.endswith(("pryce", "ward"))}
        assert gk["nbr-1-pryce"] < 30 < 75 < gk["cal-1-ward"]


def test_movement_is_low_in_calm_spells_and_high_in_attacks(match: Match,
                                                            labels: list[dict]) -> None:
    for lb in of_kind(labels, "calm"):
        assert mean_speed(match, lb["half"], lb["t_start"], lb["t_end"]) < CALM_MAX_SPEED
    for lb in of_kind(labels, "attack"):
        assert mean_speed(match, lb["half"], lb["t_start"], lb["t_end"] + 1) >= ATTACK_MIN_SPEED
    for lb in of_kind(labels, "water_break"):
        assert mean_speed(match, lb["half"], lb["t_start"], lb["t_end"]) < CALM_MAX_SPEED


# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
# 2D replay quality: actors on the ball, no teleports, explained possession
# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

MAX_ACTOR_MEDIAN_M, MAX_ACTOR_M = 2.0, 5.0
GK_MAX_FROM_GOAL_M = 25.0
MAX_BALL_MS, MAX_PLAYER_MS = 35.0, 9.5
CHAIN_GAP_M = 8.0
AWARDS = {"throw_in", "goal_kick", "corner", "free_kick", "penalty", "kickoff"}
PLAY = {"pass", "carry", "shot"}


def ball_events(match: Match) -> list:
    return [e for e in match.events if e.type in ON_BALL or e.type == "kickoff"]


def fixed(team: str, xy: tuple[float, float]) -> tuple[float, float]:
    return xy if team == "NBR" else (105 - xy[0], 68 - xy[1])


def test_acting_player_stands_at_the_event_start(match: Match) -> None:
    assert match.tracking is not None
    pos = {}
    dists = []
    for e in match.events:
        if e.player_id and e.start:
            frame = match.tracking.at(e.half, e.t)
            xy = {pid: (x, y) for pid, _, x, y in frame.players}[e.player_id]
            dists.append(math.dist(xy, fixed(e.team, e.start)))
            pos[e.id] = dists[-1]
    dists.sort()
    assert len(dists) > 1000
    assert dists[len(dists) // 2] <= MAX_ACTOR_MEDIAN_M
    assert dists[-1] <= MAX_ACTOR_M, max(pos, key=pos.get)


def test_goalkeeper_acts_near_his_own_goal(match: Match) -> None:
    by_id = match.roster.by_id()
    gk = [e for e in match.events if e.player_id and e.start
          and by_id[e.player_id].position == "GK"]
    assert len(gk) >= 10
    assert all(e.start[0] <= GK_MAX_FROM_GOAL_M for e in gk)


def test_ball_has_no_teleports_and_players_stay_below_sprint_speed(match: Match) -> None:
    assert match.tracking is not None
    worst_ball = worst_player = 0.0
    for (half, s), frame in match.tracking.frames.items():
        prev = match.tracking.frames.get((half, s - 1))
        if prev is None:
            continue
        worst_ball = max(worst_ball, math.dist(prev.ball, frame.ball))
        before = {pid: (x, y) for pid, _, x, y in prev.players}
        worst_player = max([worst_player] + [math.dist(before[pid], (x, y))
                                             for pid, _, x, y in frame.players if pid in before])
    assert worst_ball <= MAX_BALL_MS
    assert worst_player <= MAX_PLAYER_MS + 0.02  # rounding to 0.01 m


def test_after_a_save_a_corner_for_the_shooter_or_play_with_the_keepers_team(match: Match) -> None:
    evs = ball_events(match)
    saves = [i for i, e in enumerate(evs) if e.type == "save"]
    assert len(saves) >= 5
    for i in saves:
        keeper_team, nxt = evs[i].team, evs[i + 1]
        if nxt.type == "out":
            assert nxt.team == keeper_team and evs[i + 2].type == "corner"
            assert evs[i + 2].team != keeper_team
        else:
            assert nxt.team == keeper_team, nxt.id


def test_out_events_have_a_ball_path_to_the_exit_point(match: Match) -> None:
    evs = ball_events(match)
    for i, e in enumerate(evs):
        if e.type == "out" and evs[i - 1].half == e.half:
            prev = evs[i - 1]
            origin = prev.end or prev.start
            assert math.dist(fixed(prev.team, origin), fixed(e.team, e.start)) <= CHAIN_GAP_M, e.id


def test_phases_chain_where_the_previous_one_ended(match: Match) -> None:
    evs = ball_events(match)
    for a, b in zip(evs, evs[1:]):
        if a.half != b.half or b.type in AWARDS | {"goal"} or a.type == "goal":
            continue
        gap = math.dist(fixed(a.team, a.end or a.start), fixed(b.team, b.start))
        assert gap <= CHAIN_GAP_M, (a.id, b.id, gap)


def test_every_possession_change_is_explained(match: Match) -> None:
    evs = ball_events(match)
    for a, b in zip(evs, evs[1:]):
        if b.type in PLAY and a.half == b.half:
            assert a.team == b.team, (a.id, a.type, b.id, b.type)


def test_second_half_opening_calm_label_names_the_team_with_the_ball(
        match: Match, labels: list[dict]) -> None:
    for lb in of_kind(labels, "calm"):
        evs = [e for e in match.events if e.half == lb["half"] and e.type in PLAY
               and lb["t_start"] <= e.t <= lb["t_end"]]
        assert lb["note"].split(" ")[0] == evs[0].team, lb
    assert "t values 2700 to 2790 occur in both halves" in " ".join(
        (DEFAULT_OUT_DIR / "README.md").read_text().split())


def test_source_lines_stay_within_100_characters() -> None:
    root = Path(__file__).resolve().parents[1]
    for f in (root / "ingest" / "sim_placeholder.py", root / "ingest" / "loader.py",
              Path(__file__), DEFAULT_OUT_DIR / "README.md"):
        for n, line in enumerate(f.read_text().splitlines(), start=1):
            assert len(line) <= 100, (f.name, n)


# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
# Generator and loader
# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%


def test_generation_is_deterministic_and_matches_committed_files(tmp_path: Path) -> None:
    generate(tmp_path / "a")
    generate(tmp_path / "b")
    for name in ("roster.json", "events.jsonl", "tracking.csv", "labels.json", "README.md"):
        assert (tmp_path / "a" / name).read_bytes() == (tmp_path / "b" / name).read_bytes()
        assert (tmp_path / "a" / name).read_bytes() == (DEFAULT_OUT_DIR / name).read_bytes()
    assert "PLACEHOLDER" in (DEFAULT_OUT_DIR / "README.md").read_text()


def test_files_stay_under_the_size_limit() -> None:
    for f in DEFAULT_OUT_DIR.iterdir():
        assert f.stat().st_size < 50 * 1024 * 1024


def test_load_match_without_tracking() -> None:
    m = load_match(with_tracking=False)
    assert m.tracking is None and len(m.events) > 1000
    assert load_match(DEFAULT_MATCH_ID, root=SOURCE_DIR, with_tracking=True).tracking is not None


def test_load_match_missing_tracking_file(tmp_path: Path) -> None:
    folder = tmp_path / "m"
    folder.mkdir()
    for name in ("roster.json", "events.jsonl"):
        (folder / name).write_bytes((DEFAULT_OUT_DIR / name).read_bytes())
    assert load_match(root=tmp_path).tracking is None


def test_load_match_unknown_id_and_bad_event(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_match("no-such-match")
    folder = tmp_path / "m"
    folder.mkdir()
    (folder / "roster.json").write_bytes((DEFAULT_OUT_DIR / "roster.json").read_bytes())
    (folder / "events.jsonl").write_text('{"id": "e1", "t": 1, "half": 1, "team": "NBR", '
                                         '"type": "bogus"}\n')
    with pytest.raises(ValueError, match="line 1"):
        load_match(root=tmp_path)
