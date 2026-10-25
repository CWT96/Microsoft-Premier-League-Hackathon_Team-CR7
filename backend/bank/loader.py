"""Load and validate the question bank, and fill template text."""
from __future__ import annotations

import json
from pathlib import Path

import jsonschema

from backend.bank.types import MatchState, Player, Template

_DIR = Path(__file__).parent
BANK_PATH = _DIR / "templates.json"
SCHEMA_PATH = _DIR / "bank.schema.json"

_WINDOW_LABELS = {
    "5m": "the next 5 minutes",
    "10m": "the next 10 minutes",
    "20m": "the next 20 minutes",
    "half": "the rest of this half",
    "match": "this match",
}
_MINUTE_WINDOWS = {"5m": 300, "10m": 600, "20m": 1200}
_STAT_WORDS = {
    "save": "saves",
    "clearance": "clearances",
    "pass_complete": "completed passes",
    "shot": "shots",
}


def load_bank(path: Path | None = None) -> dict[str, Template]:
    """Read the bank file, validate it against the schema, and index by template id."""
    rows = json.loads((path or BANK_PATH).read_text())
    schema = json.loads(SCHEMA_PATH.read_text())
    try:
        jsonschema.validate(rows, schema)
    except jsonschema.ValidationError as e:
        raise ValueError(f"invalid question bank: {e.message}") from e
    bank: dict[str, Template] = {}
    for row in rows:
        if row["id"] in bank:
            raise ValueError(f"duplicate template id: {row['id']}")
        bank[row["id"]] = Template(**row)
    return bank


def window_label(window: str | int, phase: str | None = None) -> str:
    """Plain-English label: a window name ("5m", "half", "match") or a length in seconds.

    phase "kickoff" or "half_time" (a stack) turns "the next N minutes" into
    "the first N minutes" (of the second half, at half time). Named windows are unchanged.
    """
    if isinstance(window, str):
        if window in _MINUTE_WINDOWS:
            window = _MINUTE_WINDOWS[window]
        else:
            return _WINDOW_LABELS[window]
    if window % 60 == 0:
        minutes = window // 60
        span = "minute" if minutes == 1 else f"{minutes} minutes"
    else:
        span = f"{window} seconds"
    if phase is None:
        return f"the next {span}"
    return f"the first {span}" + (" of the second half" if phase == "half_time" else "")


def fill_text(template: Template, params: dict, state: MatchState, player: Player | None,
              phase: str | None = None) -> str:
    """Fill a template's placeholders. params["team"] is a team code; team names come from state."""
    names = state.team_names
    team = params.get("team") or state.possession_team or state.home
    opp = state.away if team == state.home else state.home
    values: dict[str, object] = {
        "team": names[team],
        "opp": names[opp],
        "home": names[state.home],
        "away": names[state.away],
        "line": params.get("line"),
        "stat": _STAT_WORDS.get(params.get("stat"), params.get("stat")),
        "player": player.name if player else None,
    }
    window = params.get("window", params.get("window_s"))
    if window is not None:
        values["window_label"] = window_label(window, phase)
    return template.text.format_map(values)
