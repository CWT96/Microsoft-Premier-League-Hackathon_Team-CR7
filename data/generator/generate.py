"""Generate the synthetic match dataset in Hudl open-data layout.

    python -m data.generator.generate            # writes data/source/
    python -m data.generator.generate --out DIR

Output (same layout as https://github.com/hudl/open-data):
    competitions.json
    matches/<competition_id>/<season_id>.json
    events/<match_id>.json
    lineups/<match_id>.json
plus sidecar files that Hudl does not have:
    extras/teams.json             team codes (NBR, CAL...) for the ingest adapter
    extras/labels/<match_id>.json the scripted story and ground-truth dull / attack spells
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .engine import MatchSim
from .matches import COMPETITION, MATCHES, REFEREES
from .rosters import COUNTRY, TEAMS, team_by_code

ROOT = Path(__file__).resolve().parents[2]
STAMP = "2026-10-25T00:00:00.000000"  # fixed, so output is byte-identical between runs


def team_block(side: str, team: dict) -> dict:
    m = team["manager"]
    return {
        f"{side}_team_id": team["team_id"], f"{side}_team_name": team["name"],
        f"{side}_team_gender": "male", f"{side}_team_group": None, "country": COUNTRY,
        "managers": [{"id": m["id"], "name": m["name"], "nickname": m["nickname"], "dob": m["dob"], "country": COUNTRY}],
    }


def simulate(cfg: dict) -> tuple[MatchSim, dict, dict]:
    home, away = team_by_code(cfg["home"]), team_by_code(cfg["away"])
    sim = MatchSim(cfg, home, away)
    sim.run()
    return sim, home, away


def match_record(cfg: dict, sim: MatchSim, home: dict, away: dict) -> dict:
    ref = REFEREES[cfg["referee"]]
    return {
        "match_id": cfg["match_id"], "match_date": cfg["date"], "kick_off": cfg["kick_off"],
        "competition": {k: COMPETITION[k] for k in ("competition_id", "country_name", "competition_name")},
        "season": {"season_id": COMPETITION["season_id"], "season_name": COMPETITION["season_name"]},
        "home_team": team_block("home", home), "away_team": team_block("away", away),
        "home_score": sim.score[0], "away_score": sim.score[1],
        "match_status": "available", "match_status_360": "unscheduled",
        "last_updated": STAMP, "last_updated_360": None,
        "metadata": {"data_version": "1.1.0", "shot_fidelity_version": "2", "xy_fidelity_version": "2"},
        "match_week": cfg["match_week"], "competition_stage": {"id": 1, "name": "Regular Season"},
        "stadium": {**home["stadium"], "country": COUNTRY},
        "referee": {**ref, "country": COUNTRY},
    }


def lineups(sim: MatchSim) -> list[dict]:
    out = []
    for tm in sim.teams:
        out.append({"team_id": tm.info["team_id"], "team_name": tm.info["name"], "lineup": [
            {"player_id": p.pid, "player_name": p.name, "player_nickname": p.nickname,
             "jersey_number": p.jersey, "country": COUNTRY, "cards": p.cards, "positions": p.positions}
            for p in tm.players]})
    return out


def events(sim: MatchSim) -> list[dict]:
    evs = sorted(sim.events, key=lambda e: (e["period"], e["_t"]))
    for i, e in enumerate(evs, 1):
        e["index"] = i
        e.pop("_t")
    return evs


def fmt_clock(s: float) -> str:
    return f"{int(s // 60):02d}:{int(s % 60):02d}"


def labels(cfg: dict, sim: MatchSim) -> dict:
    codes = [sim.teams[0].info["code"], sim.teams[1].info["code"]]
    segs = []
    for s in sim.segments:
        segs.append({
            "kind": s["kind"], "team": codes[s["team"]], "period": s["period"],
            "start": fmt_clock(s["start"]), "end": fmt_clock(s["end"]),
            "start_s": round(s["start"], 1), "end_s": round(s["end"], 1),
            "duration_s": round(s["end"] - s["start"], 1),
            "passes": s["passes"], "end_reason": s["end_reason"], "scripted": s["scripted"],
        })
    return {
        "match_id": cfg["match_id"], "slug": cfg["slug"], "seed": cfg["seed"],
        "character": cfg["character"],
        "final_score": {codes[0]: sim.score[0], codes[1]: sim.score[1]},
        "clock_note": "start_s/end_s use the StatsBomb minute clock: the second half starts at 45:00 (2700 s), "
                      "so first-half stoppage time overlaps the start of the second half. Use `period` to tell them apart.",
        "scenes": [{k: v for k, v in s.items() if k not in ("done", "at_s", "team_idx")} | {"fired": s["done"]}
                   for s in sim.scenes],
        "segments": segs,
    }


def write(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2) + "\n")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=ROOT / "data" / "source")
    args = ap.parse_args()
    out: Path = args.out

    write(out / "competitions.json", [{**COMPETITION, "match_updated": STAMP, "match_updated_360": None,
                                        "match_available_360": None, "match_available": STAMP}])
    write(out / "extras" / "teams.json", [{"team_id": t["team_id"], "name": t["name"], "code": t["code"],
                                           "formation": t["formation"]} for t in TEAMS])
    records = []
    for cfg in MATCHES:
        sim, home, away = simulate(cfg)
        records.append(match_record(cfg, sim, home, away))
        evs = events(sim)
        write(out / "events" / f"{cfg['match_id']}.json", evs)
        write(out / "lineups" / f"{cfg['match_id']}.json", lineups(sim))
        write(out / "extras" / "labels" / f"{cfg['match_id']}.json", labels(cfg, sim))
        print(f"{cfg['match_id']} {cfg['slug']}: {cfg['home']} {sim.score[0]}-{sim.score[1]} {cfg['away']}, "
              f"{len(evs)} events, {len(sim.segments)} labelled spells")
    write(out / "matches" / str(COMPETITION["competition_id"]) / f"{COMPETITION['season_id']}.json", records)


if __name__ == "__main__":
    main()
