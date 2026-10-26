"""statsbomb.py — stage [0]: map one Hudl/StatsBomb match in data/source/ to contract events.

Last Modified: 2026-10-26 15:00, first version: events, restarts, roster
Inputs:  data/source/{events,lineups}/<id>.json, data/source/extras/{teams.json,labels/<id>.json}
Outputs: contract-shaped event dicts and a ``Roster`` (see ``backend.ingest.loader``)

Workflow:
  - Keep the event types the contract has; the kept set matches
    ``data.generator.check.contract_view`` (what the question bank was calibrated on).
  - Add the dead-ball events the contract needs but Hudl folds into the kick: a restart is
    AWARDED at the previous event's second (ball out, foul) and taken by the restart pass/shot.
  - Rescale 120x80 → 105x68; second-half clock starts at 2700 s (Hudl timestamps restart).

CAUTION: ids are ``e<index>`` plus a one-letter suffix for derived events, zero-padded so that
sorting by (half, t, id) keeps a derived event next to the Hudl event it came from.
"""

from __future__ import annotations

import json
from pathlib import Path

from backend.core.constants import PITCH_LENGTH_M, PITCH_WIDTH_M, SECOND_HALF_START_S
from backend.core.types import Player, Roster

HUDL_LENGTH = 120.0
HUDL_WIDTH = 80.0

# Hudl pass/shot type → contract restart type, emitted when the restart is awarded.
RESTARTS = {"Throw-in": "throw_in", "Goal Kick": "goal_kick", "Corner": "corner",
            "Free Kick": "free_kick", "Penalty": "penalty"}
SAVES = ("Shot Saved", "Penalty Saved")
SHOT_OUTCOME = {"Goal": "goal", "Saved": "saved", "Saved To Post": "saved",
                "Off T": "off_target", "Wayward": "off_target", "Post": "off_target",
                "Blocked": "off_target"}
CARD = {"Yellow Card": "yellow", "Second Yellow": "red", "Red Card": "red"}


def _secs(e: dict) -> float:
    h, m, s = e["timestamp"].split(":")
    return round((e["period"] - 1) * SECOND_HALF_START_S + int(h) * 3600 + int(m) * 60
                 + float(s), 3)


def _xy(loc: list[float] | None) -> list[float] | None:
    if not loc:
        return None
    x = min(max(loc[0] * PITCH_LENGTH_M / HUDL_LENGTH, 0.0), PITCH_LENGTH_M)
    y = min(max(loc[1] * PITCH_WIDTH_M / HUDL_WIDTH, 0.0), PITCH_WIDTH_M)
    return [round(x, 2), round(y, 2)]


def _position(name: str) -> str:
    """Hudl position name → GK/DF/MF/FW (same grouping as ``data.generator.check.group``)."""
    if name == "Goalkeeper":
        return "GK"
    if "Back" in name:
        return "DF"
    if "Forward" in name or name.endswith("Wing"):
        return "FW"
    return "MF"


def _event(eid: str, t: float, half: int, team: str, typ: str, **extra) -> dict:
    ev = {"id": eid, "t": t, "half": half, "team": team, "type": typ}
    ev.update({k: v for k, v in extra.items() if v is not None})
    return ev


def convert_events(raw: list[dict], codes: dict[int, str]) -> list[dict]:
    """Hudl events (file order) → contract event dicts, unsorted.

    Args:
        raw: the parsed ``events/<id>.json`` list.
        codes: Hudl team id → team code.

    Returns:
        Event dicts that validate against ``contracts/event.schema.json``.
    """
    out: list[dict] = []
    prev_t: dict[int, float] = {}  # period → second of the last Hudl event seen
    ends_seen: set[int] = set()
    for e in raw:
        name, half, t = e["type"]["name"], e["period"], _secs(e)
        team = codes[e["team"]["id"]]
        pid = str(e["player"]["id"]) if "player" in e else None
        eid = f"e{e['index']:05d}"
        start = _xy(e.get("location"))
        common = {"player_id": pid, "start": start}

        if name in ("Pass", "Shot"):
            sub = e["pass" if name == "Pass" else "shot"]
            kind = sub.get("type", {}).get("name")
            if kind in RESTARTS:
                award_t = min(prev_t.get(half, t), t)
                out.append(_event(eid + "a", award_t, half, team, RESTARTS[kind], start=start))
            if kind == "Kick Off":
                out.append(_event(eid + "a", max(t - 0.01, 0.0), half, team, "kickoff",
                                  start=start))
            end = _xy(sub.get("end_location", [None])[:2]) if sub.get("end_location") else None
            if name == "Pass":
                outcome = sub.get("outcome", {}).get("name")
                out.append(_event(eid, t, half, team, "pass", end=end,
                                  outcome="incomplete" if outcome else "complete", **common))
                if outcome == "Out":
                    out.append(_event(eid + "o", round(t + e.get("duration", 0.0), 3), half,
                                      team, "out", start=end))
            else:
                result = SHOT_OUTCOME.get(sub["outcome"]["name"], "off_target")
                out.append(_event(eid, t, half, team, "shot", end=end, outcome=result, **common))
                if result == "goal":
                    out.append(_event(eid + "g", t, half, team, "goal", player_id=pid,
                                      start=end))
        elif name == "Carry":
            out.append(_event(eid, t, half, team, "carry", end=_xy(e["carry"]["end_location"]),
                              **common))
        elif name == "Goal Keeper" and e["goalkeeper"]["type"]["name"] in SAVES:
            out.append(_event(eid, t, half, team, "save", **common))
        elif name in ("Clearance", "Interception"):
            out.append(_event(eid, t, half, team, name.lower(), **common))
        elif name == "Duel" and e["duel"]["type"]["name"] == "Tackle":
            lost = "Lost" in e["duel"].get("outcome", {}).get("name", "")
            out.append(_event(eid, t, half, team, "tackle", outcome="lost" if lost else "won",
                              **common))
        elif name == "Foul Committed":
            out.append(_event(eid, t, half, team, "foul", **common))
            card = CARD.get(e.get("foul_committed", {}).get("card", {}).get("name", ""))
            if card:
                out.append(_event(eid + "c", t, half, team, "card", player_id=pid,
                                  outcome=card))
        elif name == "Substitution":
            out.append(_event(eid, t, half, team, "substitution", player_id=pid,
                              related_player_id=str(e["substitution"]["replacement"]["id"])))
        elif name == "Half End" and half not in ends_seen:
            ends_seen.add(half)
            out.append(_event(eid, t, half, team, "half_end" if half == 1 else "full_end"))
        prev_t[half] = t
    return out


def convert_roster(lineups: list[dict], home_id: int, codes: dict[int, str],
                   names: dict[int, str]) -> Roster:
    """Hudl lineups → ``Roster``; position is the player's first listed position."""
    players = []
    for team in lineups:
        for p in team["lineup"]:
            first = p["positions"][0] if p["positions"] else None
            players.append(Player(
                str(p["player_id"]), p["player_name"], codes[team["team_id"]],
                _position(first["position"]) if first else "MF", p["jersey_number"],
                bool(first and first["start_reason"] == "Starting XI")))
    away_id = next(t["team_id"] for t in lineups if t["team_id"] != home_id)
    return Roster(codes[home_id], codes[away_id], names[home_id], names[away_id],
                  tuple(players))


def find_match(match_id: str, root: Path) -> tuple[str, int]:
    """Resolve a slug ('nbr-cal-seed42') or Hudl id ('9000001') to (slug, Hudl id).

    Raises:
        FileNotFoundError: no labels file under ``root/extras/labels`` carries it.
    """
    for path in sorted((root / "extras" / "labels").glob("*.json")):
        meta = json.loads(path.read_text())
        if match_id in (meta["slug"], str(meta["match_id"])):
            return meta["slug"], int(meta["match_id"])
    raise FileNotFoundError(f"no StatsBomb match {match_id!r} under {root}")


def list_matches(root: Path) -> list[str]:
    """Slugs of every match under ``root``, in Hudl id order."""
    metas = [json.loads(p.read_text()) for p in (root / "extras" / "labels").glob("*.json")]
    return [m["slug"] for m in sorted(metas, key=lambda m: m["match_id"])]


def read_match(match_id: str, root: Path) -> tuple[str, list[dict], Roster]:
    """Read one match: (slug, contract event dicts, roster)."""
    slug, hid = find_match(match_id, root)
    teams = json.loads((root / "extras" / "teams.json").read_text())
    codes = {t["team_id"]: t["code"] for t in teams}
    names = {t["team_id"]: t["name"] for t in teams}
    meta = next(m for f in sorted((root / "matches").glob("*/*.json"))
                for m in json.loads(f.read_text()) if m["match_id"] == hid)
    raw = json.loads((root / "events" / f"{hid}.json").read_text())
    lineups = json.loads((root / "lineups" / f"{hid}.json").read_text())
    roster = convert_roster(lineups, meta["home_team"]["home_team_id"], codes, names)
    return slug, convert_events(raw, codes), roster
