"""loader.py — stage [0]: read a match folder from data/source/ into a ``Match``.

Last Modified: 2026-10-26 15:00, read the Hudl/StatsBomb dataset; placeholder format removed
Inputs:  data/source/ (Hudl/StatsBomb layout, see data/README.md), contracts/event.schema.json
Outputs: ``backend.core.types.Match``

Workflow:
  - Resolve the slug or Hudl id, convert events and roster (``backend.ingest.statsbomb``).
  - Validate every event against the contract, sort by (half, t, id).
  - The dataset has no tracking; ``Match.tracking`` is None and every stage runs without it.

CAUTION: none
"""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema

from backend.core.constants import DEFAULT_MATCH_ID
from backend.core.types import Match, MatchEvent, clock_key
from backend.ingest import statsbomb

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE_DIR = REPO_ROOT / "data" / "source"
EVENT_SCHEMA_PATH = REPO_ROOT / "contracts" / "event.schema.json"


def _to_event(raw: dict) -> MatchEvent:
    return MatchEvent(
        id=raw["id"], t=raw["t"], half=raw["half"], team=raw["team"], type=raw["type"],
        player_id=raw.get("player_id"),
        start=tuple(raw["start"]) if "start" in raw else None,
        end=tuple(raw["end"]) if "end" in raw else None,
        outcome=raw.get("outcome"), amends=raw.get("amends"),
        related_player_id=raw.get("related_player_id"))


def list_match_ids(root: Path | None = None) -> list[str]:
    """Slugs of every match in ``root`` (default ``data/source/``)."""
    return statsbomb.list_matches(SOURCE_DIR if root is None else root)


def load_match(match_id: str = DEFAULT_MATCH_ID, root: Path | None = None,
               with_tracking: bool = True) -> Match:
    """Load one match from ``root`` (default ``data/source/``).

    Args:
        match_id: Slug ('nbr-cal-seed42') or Hudl id ('9000001').
        root: Dataset root in the Hudl layout.
        with_tracking: Kept for callers; the dataset has no tracking, so always None.

    Returns:
        The match, events sorted by (half, t, id).

    Raises:
        FileNotFoundError: no match carries ``match_id``.
        ValueError: an event breaks the contract, or half_end / full_end is missing.
    """
    slug, raw_events, roster = statsbomb.read_match(match_id, SOURCE_DIR if root is None else root)
    validator = jsonschema.Draft202012Validator(json.loads(EVENT_SCHEMA_PATH.read_text()))
    for raw in raw_events:
        errors = list(validator.iter_errors(raw))
        if errors:
            raise ValueError(f"event {raw['id']}: {errors[0].message}")
    events = tuple(sorted((_to_event(r) for r in raw_events),
                          key=lambda e: (*clock_key(e.half, e.t), e.id)))
    ends = {e.type: e.t for e in events if e.type in ("half_end", "full_end")}
    if len(ends) < 2:
        raise ValueError("events must contain half_end and full_end")
    return Match(slug, roster, events, None,
                 ends["half_end"], ends["full_end"])
