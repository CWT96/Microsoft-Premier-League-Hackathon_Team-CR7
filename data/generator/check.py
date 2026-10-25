"""Validate the generated dataset: structure, the three must-haves in data/README.md, and realism.

    python -m data.generator.check               # checks data/source/
    python -m data.generator.check --dir DIR

Exit code 1 if any hard check fails. Realism gaps are printed as WARN only.
"""
from __future__ import annotations

import argparse
import collections
import json
import math
import random
import statistics as st
import sys
from pathlib import Path

from .engine import TYPE

ROOT = Path(__file__).resolve().parents[2]

# Per-match reference values measured on 7 real Hudl open-data matches (6 Premier League 2015/16
# games + one Euro 2020 game). Only the summary numbers are kept here; no real data is committed.
REFERENCE = {
    "events": (3451, 4090, 3734), "Pass": (1027, 1132, 1075), "Ball Receipt*": (939, 1077, 1014),
    "Carry": (722, 872, 819), "Pressure": (180, 382, 301), "Shot": (20, 39, 29),
    "Goal Keeper": (28, 44, 34), "Clearance": (32, 56, 41), "Ball Recovery": (92, 144, 106),
    "Duel": (51, 110, 72), "Foul Committed": (18, 32, 24), "Dribble": (16, 54, 38),
    "Interception": (3, 47, 18), "Block": (27, 59, 40), "Miscontrol": (20, 33, 25),
    "Dispossessed": (12, 35, 23), "Substitution": (5, 9, 6), "pass_completion_%": (78, 83, 81),
    "possessions": (149, 189, 173), "period_1_min": (45.9, 48.6, 46.9), "period_2_min": (48.1, 50.6, 49.4),
}
# Mean per player per match with 20+ touches, same reference matches.
REFERENCE_PLAYER = {"GK": ("saves", 3.5), "DF": ("clearances", 3.8), "MF": ("passes_completed", 40.5),
                    "FW": ("shots", 2.0)}
# The same matches seen through contracts/event.schema.json (what the question bank counts):
# per 90 minutes for starters, where a touch is any contract event by the player.
REFERENCE_CONTRACT = {
    "GK": {"touch": 41.4, "save": 3.34},
    "DF": {"touch": 90.5, "win_ball": 1.99},
    "MF": {"touch": 117.2, "shot": 1.29, "win_ball": 2.77},
    "FW": {"touch": 73.2, "shot": 2.74, "win_ball": 1.41},
}
REFERENCE_FOULS = 23.9           # per match
REFERENCE_TACKLES_LOST = 0.32    # share of tackles that fail
# Instant questions asked at a random moment inside a slow spell (one possession of 25 s+ with
# 8+ passes and no shot), settled like backend/resolve/primitives.py: share of "yes".
REFERENCE_INSTANT = {"next_pass_forward": 0.41, "keep_ball": 0.60, "reach_final_third": 0.65,
                     "next_pass_long": 0.15, "switch_play": 0.11, "back_to_keeper": 0.15}

ON_BALL = {"Pass", "Carry", "Shot", "Ball Receipt*", "Ball Recovery", "Clearance", "Interception",
           "Dribble", "Miscontrol", "Dispossessed", "Duel", "Block", "Foul Committed", "Foul Won",
           "Pressure", "Dribbled Past"}
NEEDS_END = {"Pass": "pass", "Carry": "carry", "Shot": "shot"}


def group(pos_name: str) -> str:
    if pos_name == "Goalkeeper":
        return "GK"
    if "Back" in pos_name:
        return "DF"
    if "Forward" in pos_name or pos_name.endswith("Wing"):
        return "FW"
    return "MF"


def secs(e: dict) -> float:
    h, m, s = e["timestamp"].split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def clock(v: str) -> int:
    m, s = v.split(":")
    return int(m) * 60 + int(s)


class Report:
    def __init__(self) -> None:
        self.fails: list[str] = []
        self.warns: list[str] = []

    def check(self, ok: bool, msg: str) -> None:
        if not ok:
            self.fails.append(msg)

    def warn(self, ok: bool, msg: str) -> None:
        if not ok:
            self.warns.append(msg)


def on_pitch_windows(lineups: list) -> dict[int, list[tuple[tuple, tuple]]]:
    """player_id -> list of ((period, clock_s), (period, clock_s)) windows on the pitch.

    Period comes first because first-half stoppage time (46:00...) overlaps the second half's 45:00 start.
    """
    out = {}
    for team in lineups:
        for p in team["lineup"]:
            out[p["player_id"]] = [((q["from_period"], clock(q["from"])),
                                    (q["to_period"], clock(q["to"])) if q["to"] else (9, 0)) for q in p["positions"]]
    return out


def check_match(d: Path, rec: dict, rep: Report) -> dict:
    mid = rec["match_id"]
    evs = json.loads((d / "events" / f"{mid}.json").read_text())
    lineups = json.loads((d / "lineups" / f"{mid}.json").read_text())
    labels = json.loads((d / "extras" / "labels" / f"{mid}.json").read_text())
    tag = f"[{mid}]"
    ids = {e["id"] for e in evs}
    rep.check(len(ids) == len(evs), f"{tag} duplicate event ids")
    rep.check([e["index"] for e in evs] == list(range(1, len(evs) + 1)), f"{tag} index not 1..n")

    team_of = {p["player_id"]: t["team_id"] for t in lineups for p in t["lineup"]}
    windows = on_pitch_windows(lineups)
    last = {1: -1.0, 2: -1.0}
    bad = collections.Counter()
    for e in evs:
        t = secs(e)
        if t < last[e["period"]] - 1e-6:
            bad["timestamp goes backwards"] += 1
        last[e["period"]] = t
        if e["minute"] != (e["period"] - 1) * 45 + int(t // 60):
            bad["minute does not match timestamp"] += 1
        if TYPE.get(e["type"]["name"]) != e["type"]["id"]:
            bad[f"unknown type {e['type']}"] += 1
        if any(r not in ids for r in e.get("related_events", [])):
            bad["related_events points to a missing id"] += 1
        name = e["type"]["name"]
        if name in ON_BALL and "location" not in e:
            bad[f"{name} without location"] += 1  # must-have 1
        if name in NEEDS_END and "end_location" not in e[NEEDS_END[name]]:
            bad[f"{name} without end_location"] += 1  # must-have 1
        for loc in [e.get("location"), e.get(NEEDS_END.get(name, ""), {}).get("end_location")]:
            if loc and not (0 <= loc[0] <= 120 and 0 <= loc[1] <= 80):
                bad["location off the pitch"] += 1
        if "player" in e:
            pid = e["player"]["id"]
            if team_of.get(pid) != e["team"]["id"]:
                bad["player not in this team's lineup"] += 1
            at = (e["period"], e["minute"] * 60 + e["second"])
            if name not in ("Substitution",) and not any(a <= at <= b for a, b in windows.get(pid, [])):
                bad["player acts while not on the pitch"] += 1
            if name == "Pass" and "recipient" in e["pass"] and team_of.get(e["pass"]["recipient"]["id"]) != e["team"]["id"]:
                bad["pass recipient is not a teammate"] += 1
    for k, v in bad.items():
        rep.check(False, f"{tag} {k}: {v} events")

    # score
    goals = collections.Counter(e["team"]["id"] for e in evs if e["type"]["name"] == "Shot" and e["shot"]["outcome"]["name"] == "Goal")
    h, a = rec["home_team"]["home_team_id"], rec["away_team"]["away_team_id"]
    rep.check((goals[h], goals[a]) == (rec["home_score"], rec["away_score"]),
              f"{tag} goal events {goals[h]}-{goals[a]} != match score {rec['home_score']}-{rec['away_score']}")
    rep.check(all(s["fired"] for s in labels["scenes"]), f"{tag} some scripted scenes did not fire")
    scripted_goals = sum(1 for s in labels["scenes"] if s["kind"] == "goal")
    rep.check(scripted_goals == goals[h] + goals[a], f"{tag} {scripted_goals} scripted goals but {goals[h] + goals[a]} goal events")

    # must-have 3: dull spells and attacks
    segs = labels["segments"]
    dull = [s for s in segs if s["kind"] == "dull"]
    attacks = [s for s in segs if s["kind"] == "attack"]
    attacks_shot = [s for s in attacks if s["end_reason"] in ("shot", "goal")]
    for half in (1, 2):
        nd = sum(1 for s in dull if s["period"] == half)
        rep.check(nd >= 4, f"{tag} only {nd} dull spells in half {half} (need 4+)")
    rep.check(len(attacks_shot) >= 10, f"{tag} only {len(attacks_shot)} attacks ending in a shot")
    indep = independent_dull_runs(evs)
    rep.check(len(indep) >= 8, f"{tag} only {len(indep)} midfield passing runs found directly in the events")

    # stats for the summary
    c = collections.Counter(e["type"]["name"] for e in evs)
    passes = [e for e in evs if e["type"]["name"] == "Pass"]
    stats = {k: c[k] for k in REFERENCE if k in TYPE}
    stats["events"] = len(evs)
    stats["pass_completion_%"] = round(100 * sum("outcome" not in p["pass"] for p in passes) / len(passes), 1)
    stats["possessions"] = max(e["possession"] for e in evs)
    for per in (1, 2):
        stats[f"period_{per}_min"] = round(max(secs(e) for e in evs if e["period"] == per) / 60, 1)
    return {"stats": stats, "events": evs, "lineups": lineups, "dull": dull, "attacks": attacks,
            "attacks_shot": attacks_shot, "indep": indep}


def independent_dull_runs(evs: list) -> list[tuple]:
    """Runs of 8+ consecutive passes by one team, all inside x 40-80, lasting 25 s or more."""
    runs, cur = [], []
    for e in evs:
        n = e["type"]["name"]
        if n == "Pass":
            x0, x1 = e["location"][0], e["pass"]["end_location"][0]
            same = cur and cur[-1]["team"]["id"] == e["team"]["id"] and cur[-1]["period"] == e["period"]
            if 40 <= x0 <= 80 and 40 <= x1 <= 80 and "outcome" not in e["pass"]:
                cur = cur + [e] if same else [e]
                continue
            if len(cur) >= 8 and secs(cur[-1]) - secs(cur[0]) >= 25:
                runs.append((cur[0]["period"], cur[0]["minute"], len(cur)))
            cur = []
        elif n in ("Shot", "Foul Committed", "Ball Recovery", "Interception", "Half End"):
            if len(cur) >= 8 and secs(cur[-1]) - secs(cur[0]) >= 25:
                runs.append((cur[0]["period"], cur[0]["minute"], len(cur)))
            cur = []
    return runs


def player_halves(evs: list) -> dict[str, list[int]]:
    """Must-have 2: per player and per half, the stat each position is asked about."""
    stat = {"GK": "saves", "DF": "clearances", "MF": "passes_completed", "FW": "shots"}
    per = collections.defaultdict(lambda: collections.Counter())
    grp = {}
    touches = collections.Counter()
    for e in evs:
        if "player" not in e:
            continue
        k = (e["player"]["id"], e["period"])
        grp[k] = group(e["position"]["name"])
        touches[k] += 1
        n = e["type"]["name"]
        if n == "Goal Keeper" and e["goalkeeper"]["type"]["name"] in ("Shot Saved", "Penalty Saved"):
            per[k]["saves"] += 1
        elif n == "Clearance":
            per[k]["clearances"] += 1
        elif n == "Pass" and "outcome" not in e["pass"]:
            per[k]["passes_completed"] += 1
        elif n == "Shot":
            per[k]["shots"] += 1
    out = collections.defaultdict(list)
    for k, g in grp.items():
        if touches[k] >= 10:
            out[g].append(per[k][stat[g]])
    return out


TOUCH = {"Pass", "Ball Receipt*", "Carry", "Shot", "Clearance", "Goal Keeper", "Dribble", "Ball Recovery",
         "Interception", "Miscontrol", "Block"}
WINDOWS = [15, 20, 30, 45, 60, 90, 120, 150, 180, 240, 300, 360, 420, 480]


def touch_windows(evs: list, lineups: list) -> list[tuple[str, str, int, float]]:
    """For each starter: the `player_touch_window` length whose P(touch) is closest to 0.5.

    P is measured over windows starting every 15 s while the player is on the pitch.
    """
    times = collections.defaultdict(list)
    for e in evs:
        if "player" in e and e["type"]["name"] in TOUCH:
            times[e["player"]["id"]].append((e["period"], e["minute"] * 60 + e["second"]))
    span = {per: (min(e["minute"] * 60 for e in evs if e["period"] == per),
                  max(e["minute"] * 60 + e["second"] for e in evs if e["period"] == per)) for per in (1, 2)}
    out = []
    for team in lineups:
        for p in team["lineup"]:
            if not p["positions"] or p["positions"][0]["start_reason"] != "Starting XI":
                continue
            q = p["positions"][-1]
            last = (q["to_period"], clock(q["to"])) if q["to"] else (2, span[2][1])
            ts = times[p["player_id"]]
            best = (WINDOWS[0], 0.0)
            for w in WINDOWS:
                hits = n = 0
                for per in (1, 2):
                    s = span[per][0]
                    while s + w <= span[per][1] and (per, s + w) <= last:
                        n += 1
                        hits += any(tp == per and s <= tt < s + w for tp, tt in ts)
                        s += 15
                if n and abs(hits / n - 0.5) < abs(best[1] - 0.5):
                    best = (w, hits / n)
            out.append((p["player_name"], group(p["positions"][0]["position"]), *best))
    return out


M = 120 / 105  # pitch units per metre along x (the contract pitch is 105 x 68 m)


def contract_view(evs: list) -> list[dict]:
    """The events backend/ingest/ keeps for the contract, still in the 120x80 Hudl frame."""
    out = []
    for e in evs:
        n = e["type"]["name"]
        sub = typ = outcome = None
        if n == "Pass":
            typ, sub = "pass", e["pass"]
            outcome = "incomplete" if "outcome" in sub else "complete"
        elif n == "Carry":
            typ, sub = "carry", e["carry"]
        elif n == "Shot":
            typ, sub = "shot", e["shot"]
        elif n == "Goal Keeper" and e["goalkeeper"]["type"]["name"] in ("Shot Saved", "Penalty Saved"):
            typ = "save"
        elif n == "Clearance":
            typ = "clearance"
        elif n == "Interception":
            typ = "interception"
        elif n == "Duel" and e["duel"]["type"]["name"] == "Tackle":
            typ = "tackle"
            outcome = "lost" if "Lost" in e["duel"].get("outcome", {}).get("name", "") else "won"
        elif n == "Foul Committed":
            typ = "foul"
        if typ:
            out.append({"t": secs(e), "half": e["period"], "team": e["team"]["id"], "player": e["player"]["id"],
                        "type": typ, "outcome": outcome, "start": e.get("location"),
                        "end": sub.get("end_location") if sub else None})
    return out


def contract_rates(cv: list, lineups: list, evs: list) -> tuple[dict, dict]:
    """Per-90 rates per position group for starters (until they go off), plus the minutes behind them."""
    end_s = max(e["minute"] * 60 + e["second"] for e in evs)
    by_player = collections.defaultdict(list)
    for e in cv:
        by_player[e["player"]].append(e)
    cnt, mins = collections.defaultdict(collections.Counter), collections.Counter()
    for team in lineups:
        for p in team["lineup"]:
            if not p["positions"] or p["positions"][0]["start_reason"] != "Starting XI":
                continue
            g = group(p["positions"][0]["position"])
            to = p["positions"][-1]["to"]
            mins[g] += (clock(to) if to else end_s) / 60
            for e in by_player[p["player_id"]]:
                c = cnt[g]
                c["touch"] += 1
                c["shot"] += e["type"] == "shot"
                c["save"] += e["type"] == "save"
                c["win_ball"] += e["type"] == "interception" or (e["type"] == "tackle" and e["outcome"] == "won")
    return cnt, mins


def slow_spells(evs: list) -> list[tuple[int, int, float, float]]:
    """(period, team, first pass s, last pass s) for possessions of 25 s+ with 8+ passes and no shot."""
    by = collections.defaultdict(list)
    for e in evs:
        if "possession_team" in e:
            by[e["possession"]].append(e)
    out = []
    for p in by.values():
        team = p[0]["possession_team"]["id"]
        passes = [e for e in p if e["type"]["name"] == "Pass" and e["team"]["id"] == team]
        if len(passes) >= 8 and not any(e["type"]["name"] == "Shot" for e in p):
            a, b = secs(passes[0]), secs(passes[-1])
            if b - a >= 25 and passes[0]["period"] == passes[-1]["period"]:
                out.append((passes[0]["period"], team, a, b))
    return out


def instant_answers(cv: list, spells: list, gk: dict, rng) -> collections.Counter:
    """Settle the instant templates at 3 random moments per slow spell; count the "yes" answers."""
    yes, n = collections.Counter(), 0
    for half, team, a, b in spells:
        evs = [e for e in cv if e["half"] == half]
        for _ in range(3):
            t0 = rng.uniform(a, max(a + 1, b - 5))
            win = lambda s: [e for e in evs if t0 < e["t"] <= t0 + s]  # noqa: E731
            n += 1
            nxt = next((e for e in win(15) if e["type"] == "pass" and e["team"] == team), None)
            if nxt:
                dx = nxt["end"][0] - nxt["start"][0]
                yes["next_pass_forward"] += dx > 5 * M
                yes["next_pass_long"] += math.hypot(dx / M, (nxt["end"][1] - nxt["start"][1]) * 68 / 80) >= 25
            yes["keep_ball"] += not any(e["team"] != team and e["type"] in
                                        ("pass", "carry", "shot", "clearance", "tackle", "interception") for e in win(15))
            yes["reach_final_third"] += any(e["team"] == team and e["end"] and e["end"][0] > 80 for e in win(20))
            yes["switch_play"] += any(e["type"] == "pass" and min(e["start"][1], e["end"][1]) < 80 / 3
                                      and max(e["start"][1], e["end"][1]) > 160 / 3 for e in win(15))
            yes["back_to_keeper"] += any(e["player"] == gk.get(team) for e in win(30))
    yes["n"] = n
    return yes


def fair_line(values: list[int]) -> tuple[int, float]:
    """Line L such that P(stat >= L) is closest to 0.5."""
    best = (1, 1.0)
    for line in range(1, max(values) + 2):
        p = sum(v >= line for v in values) / len(values)
        if abs(p - 0.5) < abs(best[1] - 0.5):
            best = (line, p)
    return best


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", type=Path, default=ROOT / "data" / "source")
    d = ap.parse_args().dir
    rep = Report()
    comps = json.loads((d / "competitions.json").read_text())
    records = []
    for c in comps:
        records += json.loads((d / "matches" / str(c["competition_id"]) / f"{c['season_id']}.json").read_text())

    results = {}
    for rec in records:
        results[rec["match_id"]] = check_match(d, rec, rep)

    # realism table
    print(f"{'metric':22s} {'ref min-max (mean)':>22s}  " + "  ".join(f"{m:>9d}" for m in results))
    for k, (lo, hi, mean) in REFERENCE.items():
        vals = [r["stats"][k] for r in results.values()]
        flags = []
        for v in vals:
            ok = lo * 0.8 <= v <= hi * 1.2
            flags.append(f"{v:>8}{' ' if ok else '!'}")
            rep.warn(ok, f"{k}={v} outside the real range {lo}-{hi} (±20%)")
        print(f"{k:22s} {f'{lo}-{hi} ({mean})':>22s}  " + "  ".join(flags))

    print("\nMust-have 3 (dull spells / attacks), per match:")
    for mid, r in results.items():
        print(f"  {mid}: {len(r['dull'])} labelled dull spells "
              f"(half 1: {sum(s['period'] == 1 for s in r['dull'])}, half 2: {sum(s['period'] == 2 for s in r['dull'])}), "
              f"{len(r['attacks'])} attacks of which {len(r['attacks_shot'])} end in a shot; "
              f"{len(r['indep'])} midfield passing runs found straight from the events")

    print("\nMust-have 2 (position stats, per player per half, all matches) and fair lines:")
    pooled = collections.defaultdict(list)
    for r in results.values():
        for g, v in player_halves(r["events"]).items():
            pooled[g] += v
    for g in ("GK", "DF", "MF", "FW"):
        v = pooled[g]
        stat, ref = REFERENCE_PLAYER[g]
        line, p = fair_line(v)
        print(f"  {g}: {stat:17s} mean/half {st.mean(v):5.2f} (real ≈ {ref / 2:.2f})  "
              f"fair line {line}+ → P(yes)={p:.2f}  n={len(v)}")
        rep.warn(0.35 <= p <= 0.65, f"{g}: no {stat} line gives P(yes) near 0.5 in this small sample "
                                    f"(best {line}+ at {p:.2f}); player_touch_window is the fallback")
        rep.warn(0.5 * ref / 2 <= st.mean(v) <= 1.6 * ref / 2, f"{g} {stat} per half {st.mean(v):.2f} far from real {ref / 2:.2f}")
    print("\nplayer_touch_window fallback: window with P(touch) closest to 0.5, starters, all matches:")
    tw = [row for r in results.values() for row in touch_windows(r["events"], r["lineups"])]
    for g in ("GK", "DF", "MF", "FW"):
        rows = [x for x in tw if x[1] == g]
        print(f"  {g}: median window {st.median(x[2] for x in rows):5.0f} s, "
              f"P range {min(x[3] for x in rows):.2f}-{max(x[3] for x in rows):.2f}, n={len(rows)}")
    for name, g, w, pr in tw:
        rep.check(0.4 <= pr <= 0.6, f"{name} ({g}): no touch window gives P in [0.4, 0.6] (best {w}s at {pr:.2f})")
    means = {g: st.mean(pooled[g]) for g in pooled}
    rep.check(means["FW"] > 0.3 and means["DF"] > 0.5 and means["GK"] > 0.3, "position stats look wrong")

    print("\nAs the question bank sees it (contract events), all matches vs the same real matches:")
    cnt, mins = collections.defaultdict(collections.Counter), collections.Counter()
    fouls = tackles = lost = 0
    yes = collections.Counter()
    rng = random.Random(1)
    for r in results.values():
        cv = contract_view(r["events"])
        c, m = contract_rates(cv, r["lineups"], r["events"])
        for g in m:
            cnt[g].update(c[g])
            mins[g] += m[g]
        fouls += sum(e["type"] == "foul" for e in cv)
        tackles += sum(e["type"] == "tackle" for e in cv)
        lost += sum(e["type"] == "tackle" and e["outcome"] == "lost" for e in cv)
        gk = {t["team_id"]: p["player_id"] for t in r["lineups"] for p in t["lineup"]
              if p["positions"] and p["positions"][0]["position"] == "Goalkeeper"}
        yes.update(instant_answers(cv, slow_spells(r["events"]), gk, rng))
    for g, ref in REFERENCE_CONTRACT.items():
        row = []
        for stat, real in ref.items():
            v = cnt[g][stat] / mins[g] * 90
            # Player questions are priced from real rates. Touches are common, so 3 matches pin them down;
            # saves, shots and ball wins are rare and swing more between matches, real ones included.
            tol = 0.25 if stat == "touch" else 0.4
            ok = (1 - tol) * real <= v <= (1 + tol) * real
            rep.check(ok, f"{g} {stat} per 90 = {v:.2f}, real {real} (needs ±{tol:.0%})")
            row.append(f"{stat} {v:6.2f} (real {real}){'' if ok else ' !'}")
        print(f"  {g} per 90: " + ",  ".join(row))
    fpm, lost_share = fouls / len(results), lost / max(tackles, 1)
    print(f"  fouls per match {fpm:.1f} (real {REFERENCE_FOULS}),  tackles lost {lost_share:.2f} (real {REFERENCE_TACKLES_LOST})")
    rep.warn(0.75 * REFERENCE_FOULS <= fpm <= 1.25 * REFERENCE_FOULS, f"fouls per match {fpm:.1f}, real {REFERENCE_FOULS}")
    rep.warn(0.5 * REFERENCE_TACKLES_LOST <= lost_share <= 1.5 * REFERENCE_TACKLES_LOST,
             f"tackles lost {lost_share:.2f}, real {REFERENCE_TACKLES_LOST}")
    print(f"  instant questions in slow spells (n={yes['n']}), share of yes:")
    for k, real in REFERENCE_INSTANT.items():
        v = yes[k] / yes["n"]
        print(f"    {k:18s} {v:.2f} (real {real})")
        rep.warn(abs(v - real) <= 0.12, f"instant {k}: {v:.2f} in slow spells, real {real}")

    print()
    for w in rep.warns:
        print("WARN", w)
    for f in rep.fails:
        print("FAIL", f)
    print("OK: all hard checks passed" if not rep.fails else f"{len(rep.fails)} hard check(s) failed")
    return 1 if rep.fails else 0


if __name__ == "__main__":
    sys.exit(main())
