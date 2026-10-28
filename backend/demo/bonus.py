"""Player bonus: the fan backs up to 2 players of their side and earns points from their stats.

Stats are counted per player and period from the raw StatsBomb events (definitions in
``_count``), then scored with the position's rules in ``SCORING``. A clean sheet (team conceded
nothing all match, player started) only counts at full time.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from backend.demo.fixtures import MATCHES, other_teams
from backend.demo.names import PLAYERS, SQUADS, TEAMS
from backend.ingest import statsbomb
from backend.ingest.loader import SOURCE_DIR

DEMO_MATCH_ID = "nbr-cal-seed42"
MAX_PLAYERS = 2
BIG_CHANCE_XG = 0.2
CLEAN_SHEET = "Clean sheet"

SCORING: dict[str, dict[str, Any]] = {
    "GK": {"label": "Goalkeeper", "earns": "saves, clean sheets", "rules": [
        ["Penalty save", 400], ["Clean sheet", 300], ["Save", 40], ["Claimed cross", 10]]},
    "DEF": {"label": "Defender", "earns": "tackles, clean sheets", "rules": [
        ["Goal", 400], ["Clean sheet", 200], ["Blocked shot", 30], ["Tackle won", 20],
        ["Interception", 20], ["Clearance", 10]]},
    "MID": {"label": "Midfielder", "earns": "assists, key passes", "rules": [
        ["Goal", 350], ["Assist", 200], ["Shot on target", 50], ["Key pass", 40],
        ["Tackle won", 20], ["Interception", 20]]},
    "FWD": {"label": "Forward", "earns": "goals, shots", "rules": [
        ["Goal", 300], ["Assist", 200], ["Big chance made", 60], ["Shot on target", 50],
        ["Dribble past", 20], ["Won a foul", 10]]},
}

_WON = {"Won", "Success", "Success In Play", "Success Out"}
_ON_TARGET = {"Saved", "Saved To Post"}
_GK = {"Shot Saved": "Save", "Penalty Saved": "Penalty save", "Collected": "Claimed cross"}

# player id -> (display team code, position, starter)
_ROSTER: dict[str, tuple[str, str, bool]] = {
    pid: (code, pos, starter) for code, (_, _, rows) in SQUADS.items()
    for pid, pos, starter in rows}


@dataclass(frozen=True)
class Stats:
    stats: dict[tuple[str, int], Counter]  # (player id, period) -> stat name -> count
    conceded: dict[str, int]  # display team code -> goals conceded in the match


def squads() -> dict:
    """Payload of ``GET /demo/squads``."""
    teams = {}
    for code, (internal, popular, rows) in SQUADS.items():
        teams[code] = {"name": TEAMS[internal]["name"], "popular": popular, "players": [
            {"id": pid, "name": PLAYERS[pid][0], "num": PLAYERS[pid][1], "pos": pos,
             "starter": starter} for pid, pos, starter in rows]}
    # the other matches on the pick screen: browsable squads, the run plays Northbridge v Calder
    return {"teams": teams, "scoring": SCORING, "matches": MATCHES, "other_teams": other_teams()}


def sanitise(team: Any, raw: Any) -> list[str]:
    """At most 2 distinct known ids of ``team``'s squad, in the order given; anything else dropped."""
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for pid in raw:
        if (isinstance(pid, str) and pid in _ROSTER and _ROSTER[pid][0] == team
                and pid not in out):
            out.append(pid)
        if len(out) == MAX_PLAYERS:
            break
    return out


def card(pid: str) -> dict:
    """{id, name, num, pos} of a squad player."""
    return {"id": pid, "name": PLAYERS[pid][0], "num": PLAYERS[pid][1], "pos": _ROSTER[pid][1]}


def _count(e: dict, shots: dict[str, dict]) -> list[str]:
    """Stat names one raw event earns its player (see global constraints for the definitions)."""
    kind = e["type"]["name"]
    if kind == "Shot":
        outcome = e["shot"].get("outcome", {}).get("name")
        return ["Goal"] if outcome == "Goal" else ["Shot on target"] if outcome in _ON_TARGET else []
    if kind == "Pass":
        p = e["pass"]
        out = ["Assist"] if p.get("goal_assist") else []
        if p.get("shot_assist"):
            out.append("Key pass")
            xg = shots.get(p.get("assisted_shot_id"), {}).get("statsbomb_xg")
            if xg is None or xg >= BIG_CHANCE_XG:  # no xG in the data: every key pass counts
                out.append("Big chance made")
        return out
    if kind == "Dribble":
        return ["Dribble past"] if e["dribble"].get("outcome", {}).get("name") == "Complete" else []
    if kind == "Foul Won":
        return ["Won a foul"]
    if kind == "Duel":
        d = e.get("duel", {})
        won = d.get("type", {}).get("name") == "Tackle" and d.get("outcome", {}).get("name") in _WON
        return ["Tackle won"] if won else []
    if kind == "Interception":
        won = e.get("interception", {}).get("outcome", {}).get("name") in _WON
        return ["Interception"] if won else []
    if kind == "Clearance":
        return ["Clearance"]
    if kind == "Block":
        return ["Blocked shot"]
    if kind == "Goal Keeper":
        stat = _GK.get(e.get("goalkeeper", {}).get("type", {}).get("name"))
        return [stat] if stat else []
    return []


def stats_from_events(raw: list[dict], team_codes: dict[int, str]) -> Stats:
    """Per-player, per-period stat counts and goals conceded per display team code."""
    stats: dict[tuple[str, int], Counter] = {(pid, h): Counter() for pid in _ROSTER for h in (1, 2)}
    shots = {e["id"]: e["shot"] for e in raw if e["type"]["name"] == "Shot"}
    scored: Counter = Counter()
    for e in raw:
        if "player" not in e:
            continue
        names = _count(e, shots)
        if "Goal" in names:
            scored[team_codes.get(e["team"]["id"], "")] += 1
        if names:
            stats.setdefault((str(e["player"]["id"]), e["period"]), Counter()).update(names)
    codes = list(SQUADS)
    conceded = {c: sum(n for t, n in scored.items() if t != c) for c in codes}
    return Stats(stats, conceded)


@lru_cache(maxsize=1)
def match_stats(match_id: str = DEMO_MATCH_ID) -> Stats:
    """Stats of the demo match, read once from the raw StatsBomb events file."""
    _, hid = statsbomb.find_match(match_id, SOURCE_DIR)
    internal = {t["team_id"]: t["code"]
                for t in json.loads((SOURCE_DIR / "extras" / "teams.json").read_text())}
    team_codes = {tid: TEAMS[c]["code"] for tid, c in internal.items() if c in TEAMS}
    raw = json.loads((SOURCE_DIR / "events" / f"{hid}.json").read_text())
    return stats_from_events(raw, team_codes)


def player_bonus(pid: str, halves: tuple[int, ...] = (1, 2), clean_sheet: bool = False,
                 st: Stats | None = None) -> dict:
    """{id, name, num, pos, points, stats: [{what, count, pts}]} over ``halves``.

    ``clean_sheet`` adds the clean-sheet rule (full time only). Only stats with count > 0.
    Unknown ids give 0 points and no stats.
    """
    if pid not in _ROSTER:
        name, num = PLAYERS.get(pid, ("", 0))
        return {"id": pid, "name": name, "num": num, "pos": "", "points": 0, "stats": []}
    st = st or match_stats()
    team, pos, starter = _ROSTER[pid]
    counts: Counter = Counter()
    for h in halves:
        counts.update(st.stats.get((pid, h), Counter()))
    if clean_sheet and starter and st.conceded.get(team, 1) == 0:
        counts[CLEAN_SHEET] += 1
    rows = [{"what": what, "count": counts[what], "pts": counts[what] * pts}
            for what, pts in SCORING[pos]["rules"] if counts[what] > 0]
    return {**card(pid), "points": sum(r["pts"] for r in rows), "stats": rows}
