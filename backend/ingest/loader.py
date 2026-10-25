"""loader.py — stage [0]: read a match folder from data/source/ into a ``Match``.

Last Modified: 2026-10-24 22:30, first version: roster, events, optional tracking
Inputs:  data/source/<folder>/{roster.json, events.jsonl, tracking.csv}, contracts/event.schema.json
Outputs: ``backend.core.types.Match``

Workflow:
  - Find the folder whose roster.json carries the requested match_id.
  - Validate every event against the contract, sort by (half, t, id).
  - Read tracking.csv into 1 Hz frames unless disabled or missing.

CAUTION: none
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import jsonschema

from backend.core.constants import DEFAULT_MATCH_ID
from backend.core.types import (Match, MatchEvent, Player, Roster, Tracking, TrackingFrame,
                                clock_key)

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE_DIR = REPO_ROOT / "data" / "source"
EVENT_SCHEMA_PATH = REPO_ROOT / "contracts" / "event.schema.json"
BALL_ID = "ball"


def _find_folder(match_id: str, root: Path) -> Path:
    for roster_path in sorted(root.glob("*/roster.json")):
        if json.loads(roster_path.read_text()).get("match_id") == match_id:
            return roster_path.parent
    raise FileNotFoundError(f"no match folder with match_id {match_id!r} under {root}")


def _read_roster(folder: Path) -> Roster:
    raw = json.loads((folder / "roster.json").read_text())
    players = tuple(Player(p["player_id"], p["name"], p["team"], p["position"], p["number"],
                           p.get("starter", True)) for p in raw["players"])
    return Roster(raw["home"], raw["away"], raw["home_name"], raw["away_name"], players)


def _read_events(folder: Path) -> tuple[MatchEvent, ...]:
    validator = jsonschema.Draft202012Validator(json.loads(EVENT_SCHEMA_PATH.read_text()))
    events = []
    for n, line in enumerate((folder / "events.jsonl").read_text().splitlines(), start=1):
        if not line.strip():
            continue
        raw = json.loads(line)
        errors = list(validator.iter_errors(raw))
        if errors:
            raise ValueError(f"events.jsonl line {n}: {errors[0].message}")
        events.append(MatchEvent(
            id=raw["id"], t=raw["t"], half=raw["half"], team=raw["team"], type=raw["type"],
            player_id=raw.get("player_id"),
            start=tuple(raw["start"]) if "start" in raw else None,
            end=tuple(raw["end"]) if "end" in raw else None,
            outcome=raw.get("outcome"), amends=raw.get("amends"),
            related_player_id=raw.get("related_player_id")))
    return tuple(sorted(events, key=lambda e: (*clock_key(e.half, e.t), e.id)))


def _read_tracking(path: Path) -> Tracking:
    players: dict[tuple[int, int], list[tuple[str, str, float, float]]] = {}
    balls: dict[tuple[int, int], tuple[float, float]] = {}
    with path.open(newline="") as fh:
        for row in csv.DictReader(fh):
            key = (int(row["half"]), int(float(row["t"])))
            xy = (float(row["x"]), float(row["y"]))
            if row["player_id"] == BALL_ID:
                balls[key] = xy
            else:
                players.setdefault(key, []).append((row["player_id"], row["team"], *xy))
    frames = {key: TrackingFrame(float(key[1]), key[0], tuple(rows), balls.get(key))
              for key, rows in players.items()}
    return Tracking(frames)


def load_match(match_id: str = DEFAULT_MATCH_ID, root: Path | None = None,
               with_tracking: bool = True) -> Match:
    """Load one match from ``root`` (default ``data/source/``).

    Args:
        match_id: Id found in a folder's roster.json.
        root: Folder holding one sub-folder per match.
        with_tracking: Read tracking.csv when it exists; otherwise ``tracking`` is None.

    Returns:
        The match, events sorted by (half, t, id).

    Raises:
        FileNotFoundError: no folder carries ``match_id``.
        ValueError: an event breaks the contract, or half_end / full_end is missing.
    """
    folder = _find_folder(match_id, SOURCE_DIR if root is None else root)
    events = _read_events(folder)
    ends = {e.type: e.t for e in events if e.type in ("half_end", "full_end")}
    if len(ends) < 2:
        raise ValueError("events must contain half_end and full_end")
    tracking_path = folder / "tracking.csv"
    tracking = _read_tracking(tracking_path) if with_tracking and tracking_path.exists() else None
    return Match(match_id, _read_roster(folder), events, tracking,
                 ends["half_end"], ends["full_end"])
