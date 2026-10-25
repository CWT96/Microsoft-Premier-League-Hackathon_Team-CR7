"""Possession-by-possession match simulator that writes Hudl / StatsBomb-format events.

Conventions copied from Hudl open-data:
- 120x80 pitch, every event's location is in the *acting* team's frame (it attacks towards x=120).
- `timestamp` restarts at 00:00:00.000 each period; `minute` keeps counting (second half starts at 45).
- A pass with no `outcome` is complete.

Goals, red cards, penalties and some dull spells / chances are scripted per match (see matches.py),
so every match has a known story. Everything in between is simulated from the team style knobs.
Unscripted shots never go in, which keeps the scoreline exactly as scripted.
"""
from __future__ import annotations

import math
import random
import uuid
from dataclasses import dataclass, field

from .rosters import FORMATIONS, POSITIONS, player_id

# ---------------------------------------------------------------- vocabulary (ids from real Hudl files)
TYPE = {
    "Ball Recovery": 2, "Dispossessed": 3, "Duel": 4, "Block": 6, "Clearance": 9, "Interception": 10,
    "Dribble": 14, "Shot": 16, "Pressure": 17, "Half Start": 18, "Substitution": 19, "Foul Won": 21,
    "Foul Committed": 22, "Goal Keeper": 23, "Pass": 30, "Half End": 34, "Starting XI": 35,
    "Miscontrol": 38, "Dribbled Past": 39, "Ball Receipt*": 42, "Carry": 43,
}
PATTERN = {
    "Regular Play": 1, "From Corner": 2, "From Free Kick": 3, "From Throw In": 4, "Other": 5,
    "From Counter": 6, "From Goal Kick": 7, "From Keeper": 8, "From Kick Off": 9,
}
PASS_OUTCOME = {"Incomplete": 9, "Out": 75, "Pass Offside": 76}
PASS_HEIGHT = {"Ground Pass": 1, "Low Pass": 2, "High Pass": 3}
PASS_TYPE = {"Corner": 61, "Free Kick": 62, "Goal Kick": 63, "Interception": 64, "Kick Off": 65,
             "Recovery": 66, "Throw-in": 67}
BODY = {"Head": 37, "Left Foot": 38, "Right Foot": 40, "Drop Kick": 68, "Keeper Arm": 69, "Other": 70}
SHOT_OUTCOME = {"Blocked": 96, "Goal": 97, "Off T": 98, "Post": 99, "Saved": 100, "Wayward": 101}
SHOT_TYPE = {"Free Kick": 62, "Open Play": 87, "Penalty": 88}
TECH = {"Half Volley": 91, "Normal": 93, "Volley": 95}
GK_TYPE = {"Collected": 25, "Goal Conceded": 26, "Penalty Conceded": 28, "Penalty Saved": 29,
           "Shot Faced": 32, "Shot Saved": 33}
GK_OUTCOME = {"Success": 15, "In Play Danger": 52, "In Play Safe": 53, "No Touch": 55, "Touched Out": 59}
CARD = {"Red Card": 5, "Second Yellow": 6, "Yellow Card": 7}
DUEL_TYPE = {"Aerial Lost": 10, "Tackle": 11}
DUEL_OUTCOME = {"Won": 4, "Success In Play": 16}

HALF_SECS = 45 * 60


def named(table: dict, name: str) -> dict:
    return {"id": table[name], "name": name}


def r1(v: float) -> float:
    """One decimal, always a float (Hudl files never store integer coordinates)."""
    return round(float(v), 1)


def clip(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def flip(loc: tuple[float, float]) -> tuple[float, float]:
    """Convert a location between the two teams' frames."""
    return (120.0 - loc[0], 80.0 - loc[1])


def xg(loc: tuple[float, float], header: bool = False) -> float:
    """Simple distance + goal-mouth-angle model, roughly matching StatsBomb xG levels."""
    dx, dy = 120.0 - loc[0], loc[1] - 40.0
    dist = math.hypot(dx, dy)
    angle = math.atan2(8 * dx, dx * dx + dy * dy - 16) if dx > 0 else 0.0
    if angle < 0:
        angle += math.pi
    logit = -1.15 - 0.115 * dist + 1.55 * angle - (0.9 if header else 0.0)
    return 1 / (1 + math.exp(-logit))


def soft_band(v: float, lo: float, hi: float, rng: random.Random) -> float:
    """Pull a value back inside [lo, hi] without piling everything up on the edges."""
    if v < lo:
        return lo + rng.uniform(0, 8)
    if v > hi:
        return hi - rng.uniform(0, 8)
    return v


def mmss(minute: int, second: int) -> str:
    return f"{minute:02d}:{second:02d}"


# ---------------------------------------------------------------- state
@dataclass
class Player:
    pid: int
    name: str
    nickname: str | None
    jersey: int
    team_idx: int
    pos_id: int | None = None
    yellow: bool = False
    positions: list = field(default_factory=list)  # lineup "positions" entries
    cards: list = field(default_factory=list)

    @property
    def group(self) -> str:
        return POSITIONS[self.pos_id][1]

    @property
    def home(self) -> tuple[float, float]:
        return POSITIONS[self.pos_id][2]


@dataclass
class Team:
    idx: int
    info: dict
    style: dict
    players: list[Player]
    onpitch: list[Player]
    bench: list[Player]
    subs_left: list[float]  # match minutes at which a substitution is due

    @property
    def ref(self) -> dict:
        return {"id": self.info["team_id"], "name": self.info["name"]}


class HalfOver(Exception):
    pass


@dataclass
class Start:
    """How the next possession starts."""
    team: int
    player: Player
    loc: tuple[float, float]
    restart: str | None = None      # kickoff, throw_in, goal_kick, corner, free_kick, keeper, penalty
    pattern: str = "Regular Play"
    mode: str = "build"
    via: str | None = None          # "Recovery" / "Interception": type of the first pass after a turnover
    scene: dict | None = None       # scripted scene that continues through this restart
    prev: dict | None = None        # event the first touch relates to


# ---------------------------------------------------------------- the simulator
class MatchSim:
    def __init__(self, cfg: dict, home: dict, away: dict):
        self.cfg = cfg
        self.rng = random.Random(cfg["seed"])
        self.teams = [self._make_team(0, home), self._make_team(1, away)]
        self.events: list[dict] = []
        self.period = 1
        self.t = 0.0
        self.half_len = HALF_SECS + cfg["stoppage"][0] * 60 + self.rng.uniform(0, 50)
        self.possession = 0
        self.poss_team = 0
        self.pattern = "Regular Play"
        self.score = [0, 0]
        self.scenes = sorted(
            [dict(s, done=False, at_s=self._parse_at(s["at"]), team_idx=0 if s["team"] == "home" else 1)
             for s in cfg["scenes"]],
            key=lambda s: s["at_s"],
        )
        self.last_pass: dict | None = None
        self.segments: list[dict] = []   # ground-truth dull / attack spells
        self.seg: dict | None = None

    # ------------------------------------------------------------ setup
    def _make_team(self, idx: int, info: dict) -> Team:
        players = []
        for i, (jersey, name, nick) in enumerate(info["squad"]):
            p = Player(player_id(info, jersey), name, nick, jersey, idx)
            if i < 11:
                p.pos_id = FORMATIONS[info["formation"]][i]
                p.positions.append({"position_id": p.pos_id, "position": POSITIONS[p.pos_id][0],
                                    "from": "00:00", "to": None, "from_period": 1, "to_period": None,
                                    "start_reason": "Starting XI", "end_reason": "Final Whistle"})
            players.append(p)
        n_subs = self.cfg["subs"][idx]
        subs = sorted(self.rng.uniform(56, 86) for _ in range(n_subs))
        return Team(idx, info, info["style"], players, players[:11], players[11:], subs)

    @staticmethod
    def _parse_at(at: str) -> float:
        m, s = at.split(":")
        return int(m) * 60 + int(s)

    # ------------------------------------------------------------ clock helpers
    def minute_second(self, t: float | None = None) -> tuple[int, int]:
        t = self.t if t is None else t
        return (self.period - 1) * 45 + int(t // 60), int(t % 60)

    def match_clock(self, t: float | None = None) -> float:
        """Match clock in seconds with the second half starting at 45:00 (StatsBomb `minute` style)."""
        m, s = self.minute_second(t)
        t = self.t if t is None else t
        return m * 60 + s + (t - int(t))

    def tick(self, secs: float) -> None:
        self.t += secs

    def check_half(self, in_attack: bool) -> None:
        if self.t >= self.half_len + (25 if in_attack else 0):
            raise HalfOver

    # ------------------------------------------------------------ emit
    def emit(self, etype: str, team: int | None, player: Player | None = None, loc=None,
             dur: float = 0.0, sub: dict | None = None, t: float | None = None,
             pressure: bool = False, related: list | None = None, out: bool = False,
             extra: dict | None = None) -> dict:
        t = round(max(0.0, self.t if t is None else t), 3)  # ms precision, so 59.9996 can't print as 60.000
        minute, second = self.minute_second(t)
        hh, rem = divmod(t, 3600)
        mm, ss = divmod(rem, 60)
        e = {
            "id": str(uuid.UUID(int=self.rng.getrandbits(128), version=4)),
            "index": 0,
            "period": self.period,
            "timestamp": f"{int(hh):02d}:{int(mm):02d}:{ss:06.3f}",
            "minute": minute,
            "second": second,
            "type": named(TYPE, etype),
            "possession": self.possession,
            "possession_team": self.teams[self.poss_team].ref,
            "play_pattern": named(PATTERN, self.pattern),
        }
        if team is not None:
            e["team"] = self.teams[team].ref
        if player is not None:
            e["player"] = {"id": player.pid, "name": player.name}
            e["position"] = {"id": player.pos_id, "name": POSITIONS[player.pos_id][0]}
        if loc is not None:
            e["location"] = [r1(clip(loc[0], 0, 120)), r1(clip(loc[1], 0, 80))]
        e["duration"] = round(dur, 6)
        if pressure:
            e["under_pressure"] = True
        if out:
            e["out"] = True
        e["related_events"] = list(related or [])
        if extra:
            e.update(extra)
        if sub:
            key = {"Ball Receipt*": "ball_receipt", "Foul Committed": "foul_committed",
                   "Foul Won": "foul_won", "Goal Keeper": "goalkeeper"}.get(etype, etype.lower().replace(" ", "_"))
            e[key] = sub
        e["_t"] = t  # internal sort key, stripped on write
        self.events.append(e)
        return e

    @staticmethod
    def link(a: dict, b: dict) -> None:
        a["related_events"].append(b["id"])
        b["related_events"].append(a["id"])

    # ------------------------------------------------------------ positions
    def att_pos(self, p: Player, ball) -> tuple[float, float]:
        hx, hy = p.home
        if p.group == "GK":
            return (clip(8 + 0.08 * (ball[0] - 60), 3, 20), clip(40 + 0.1 * (ball[1] - 40), 34, 46))
        x = hx + 0.5 * (ball[0] - 60)
        if p.group == "DF" and p.pos_id not in (2, 6, 7, 8):
            x = min(x, 72)
        return (clip(x, 4, 116), clip(hy + 0.2 * (ball[1] - 40), 2, 78))

    def def_pos(self, p: Player, ball_def) -> tuple[float, float]:
        """Shape of the team without the ball; `ball_def` is in that team's own frame."""
        hx, hy = p.home
        if p.group == "GK":
            return (clip(5 + 0.05 * (ball_def[0] - 50), 1.5, 14), clip(40 + 0.1 * (ball_def[1] - 40), 35, 45))
        x = 0.7 * hx + 0.45 * (ball_def[0] - 50) + 5
        return (clip(x, 3, 110), clip(hy + 0.35 * (ball_def[1] - 40), 2, 78))

    def nearest_def(self, team: int, ball, exclude_gk: bool = True, spread: float = 6.0) -> tuple[Player, tuple]:
        """Pick a defender of `team` near the ball (ball given in the *attacking* frame)."""
        bd = flip(ball)
        cands = [p for p in self.teams[team].onpitch if not (exclude_gk and p.group == "GK")]
        locs = [self.def_pos(p, bd) for p in cands]
        w = [math.exp(-math.hypot(l[0] - bd[0], l[1] - bd[1]) / spread) + 1e-6 for l in locs]
        i = self.rng.choices(range(len(cands)), weights=w)[0]
        return cands[i], locs[i]

    def gk(self, team: int) -> Player:
        return next(p for p in self.teams[team].onpitch if p.group == "GK")

    def find(self, team: int, name: str | None, groups=("FW",)) -> Player:
        on = self.teams[team].onpitch
        if name:
            for p in on:
                if name.lower() in p.name.lower():
                    return p
        pool = [p for p in on if p.group in groups] or [p for p in on if p.group != "GK"]
        return self.rng.choice(pool)

    # ------------------------------------------------------------ segments (ground-truth labels)
    def seg_open(self, kind: str, team: int, scripted: bool) -> None:
        if self.seg and self.seg["kind"] == kind and self.seg["team"] == team:
            return
        self.seg_close("mode_change")
        self.seg = {"kind": kind, "team": team, "scripted": scripted, "period": self.period,
                    "start": self.match_clock(), "start_ts": self.t, "passes": 0}

    def seg_close(self, reason: str | None) -> None:
        s, self.seg = self.seg, None
        if not s:
            return
        s["end"] = self.match_clock()
        s["end_reason"] = reason
        dur = s["end"] - s["start"]
        if s["kind"] == "dull" and (dur < 25 or s["passes"] < 8):
            return
        if s["kind"] == "attack" and dur < 2:
            return
        self.segments.append(s)

    # ------------------------------------------------------------ scenes
    def take_scene(self, team: int) -> dict | None:
        now = self.match_clock()
        for s in self.scenes:
            if s["done"] or s["at_s"] > now or s.get("kind") == "red":
                continue
            if s["team_idx"] == team:
                s["done"] = True
                return s
            break  # an earlier scene for the other team is still waiting; keep order
        return None

    def due_red(self, fouling_team: int) -> dict | None:
        now = self.match_clock()
        for s in self.scenes:
            if not s["done"] and s["kind"] == "red" and s["team_idx"] == fouling_team and s["at_s"] <= now:
                return s
        return None

    # ------------------------------------------------------------ the match
    def run(self) -> None:
        for tm in self.teams:
            lineup = [{"player": {"id": p.pid, "name": p.name},
                       "position": {"id": p.pos_id, "name": POSITIONS[p.pos_id][0]},
                       "jersey_number": p.jersey} for p in tm.onpitch]
            self.emit("Starting XI", tm.idx, extra={"tactics": {"formation": tm.info["formation"], "lineup": lineup}})
        for period in (1, 2):
            self.period = period
            self.t = 0.0
            if period == 2:
                self.half_len = HALF_SECS + self.cfg["stoppage"][1] * 60 + self.rng.uniform(0, 50)
            kick_team = 0 if period == 1 else 1
            self.poss_team = kick_team
            self.pattern = "Regular Play"
            a = self.emit("Half Start", 0)
            b = self.emit("Half Start", 1)
            self.link(a, b)
            if period == 2:
                self.ht_subs()
            start = self.kickoff_start(kick_team)
            try:
                while True:
                    start = self.play(start)
            except HalfOver:
                self.seg_close("half_end")
                a = self.emit("Half End", 0)
                b = self.emit("Half End", 1)
                self.link(a, b)

    def kickoff_start(self, team: int) -> Start:
        taker = self.find(team, None, ("FW",))
        return Start(team, taker, (60.0, 40.0), restart="kickoff", pattern="From Kick Off", mode="build")

    # ------------------------------------------------------------ substitutions
    def ht_subs(self) -> None:
        for tm in self.teams:
            if tm.subs_left and self.rng.random() < 0.25:
                tm.subs_left.pop(0)
                self.substitute(tm, ht=True)

    def maybe_subs(self) -> None:
        """Called at dead balls."""
        now_min = self.match_clock() / 60
        for tm in self.teams:
            while tm.subs_left and tm.subs_left[0] <= now_min and self.period == 2:
                tm.subs_left.pop(0)
                self.substitute(tm)
                self.tick(self.rng.uniform(20, 35))

    def substitute(self, tm: Team, ht: bool = False) -> None:
        protected = {n.lower() for n in self.cfg.get("full_match", [])}
        outs = [p for p in tm.onpitch if p.group != "GK" and not any(k in p.name.lower() for k in protected)]
        if not outs or not tm.bench:
            return
        weights = [{"FW": 3, "MF": 2.5, "DF": 1}[p.group] for p in outs]
        off = self.rng.choices(outs, weights=weights)[0]
        same = [b for b in tm.bench if b.jersey and self._bench_group(tm, b) == off.group]
        on = (same or [b for b in tm.bench if self._bench_group(tm, b) != "GK"])[0]
        tm.bench.remove(on)
        on.pos_id = off.pos_id
        m, s = (45, 0) if ht else self.minute_second()
        period = 2
        off.positions[-1].update({"to": mmss(m, s), "to_period": period,
                                  "end_reason": "Substitution - Off (Tactical)"})
        on.positions.append({"position_id": on.pos_id, "position": POSITIONS[on.pos_id][0],
                             "from": mmss(m, s), "to": None, "from_period": period, "to_period": None,
                             "start_reason": "Substitution - On (Tactical)", "end_reason": "Final Whistle"})
        tm.onpitch[tm.onpitch.index(off)] = on
        self.emit("Substitution", tm.idx, off, t=0.0 if ht else None,
                  sub={"outcome": {"id": 103, "name": "Tactical"}, "replacement": {"id": on.pid, "name": on.name}})

    @staticmethod
    def _bench_group(tm: Team, p: Player) -> str:
        i = tm.players.index(p) - 11
        return ["GK", "DF", "DF", "MF", "MF", "FW", "FW"][i]

    def send_off(self, p: Player, reason: str) -> None:
        tm = self.teams[p.team_idx]
        m, s = self.minute_second()
        p.positions[-1].update({"to": mmss(m, s), "to_period": self.period, "end_reason": reason})
        tm.onpitch.remove(p)

    # ------------------------------------------------------------ one possession
    def play(self, st: Start) -> Start:
        """Simulate one possession; return how the next one starts."""
        if st.restart in ("throw_in", "goal_kick", "corner", "free_kick", "kickoff", "keeper"):
            self.maybe_subs()
        if st.player not in self.teams[st.team].onpitch:  # taker was substituted or sent off at the stoppage
            same = [p for p in self.teams[st.team].onpitch if p.group == st.player.group]
            st.player = (same or [p for p in self.teams[st.team].onpitch if p.group != "GK"])[0]
        if st.team != self.poss_team or st.restart == "kickoff" or self.possession == 0:
            self.possession += 1
        self.poss_team = st.team
        self.pattern = st.pattern
        self.check_half(False)
        scene = st.scene or self.take_scene(st.team)
        return Possession(self, st, scene).run()


class Possession:
    """Touch-by-touch loop for the team in possession."""

    def __init__(self, sim: MatchSim, st: Start, scene: dict | None):
        self.sim, self.rng = sim, sim.rng
        self.team = st.team
        self.opp = 1 - st.team
        self.tm = sim.teams[st.team]
        self.style = self.tm.style
        self.carrier = st.player
        self.ball = st.loc
        self.st = st
        self.mode = st.mode
        self.scene = scene
        self.passes = 0
        self.dull_until = None
        self.pressure = False
        self.prev_evt: dict | None = st.prev  # last receipt / recovery, for related_events
        if scene and scene["kind"] == "dull":
            self.mode = "dull"
            self.dull_until = sim.match_clock() + scene["secs"]
        elif scene:
            self.mode = "scripted"

    # ------------------------------------------------------------ main loop
    def run(self) -> Start:
        sim = self.sim
        if self.st.restart:
            nxt = self.restart(self.st.restart)
            if nxt:
                return nxt
        while True:
            if self.mode != "scripted":  # a scripted goal is never cut off by the half-time whistle
                sim.check_half(self.mode in ("attack", "counter"))
            self.update_mode()
            nxt = self.touch()
            if nxt:
                return nxt

    def update_mode(self) -> None:
        sim, x = self.sim, self.ball[0]
        if self.mode == "dull":
            if sim.match_clock() >= self.dull_until:
                self.mode = "build"
            else:
                sim.seg_open("dull", self.team, True)
                return
        if self.mode == "scripted":
            sim.seg_open("attack", self.team, True)
            return
        if self.mode == "counter" or x > 80:
            if self.mode != "counter":
                self.mode = "attack"
            sim.seg_open("attack", self.team, False)
        elif 40 <= x <= 80:
            if self.mode == "attack" and x < 70:
                self.mode = "circulate"
            elif self.mode == "build" and self.passes >= 2:
                self.mode = "circulate"
            if self.mode == "circulate":
                sim.seg_open("dull", self.team, False)
            else:
                sim.seg_close("mode_change")
        else:
            self.mode = "build"
            sim.seg_close("mode_change")

    # ------------------------------------------------------------ restarts
    def restart(self, kind: str) -> Start | None:
        sim, r = self.sim, self.rng
        if kind == "kickoff":
            mates = [p for p in self.tm.onpitch if p.group == "MF"]
            to = r.choice(mates)
            end = (r.uniform(42, 50), r.uniform(30, 50))
            return self.pass_to(to, end, ptype="Kick Off", force=True)
        if kind == "throw_in":
            return self.pass_to(*self.pick_recipient(max_len=22), ptype="Throw-in",
                                height=r.choice(["Low Pass", "High Pass"]), body=None, acc=0.86)
        if kind == "goal_kick":
            self.ball = (6.0, r.choice([30.1, 34.1, 45.9, 49.9]))
            self.carrier = sim.gk(self.team)
            if r.random() < 0.45 + 0.3 * (1 - self.style["direct"]):
                cbs = [p for p in self.tm.onpitch if p.group == "DF"]
                to = r.choice(cbs)
                end = sim.att_pos(to, (30, 40))
                return self.pass_to(to, end, ptype="Goal Kick", force=True)
            to = r.choice([p for p in self.tm.onpitch if p.group in ("MF", "FW")])
            end = (r.uniform(55, 80), r.uniform(10, 70))
            return self.pass_to(to, end, ptype="Goal Kick", height="High Pass", acc=0.5)
        if kind == "keeper":
            self.carrier = sim.gk(self.team)
            if r.random() < 0.5:
                to = r.choice([p for p in self.tm.onpitch if p.group == "DF"])
                return self.pass_to(to, sim.att_pos(to, (35, 40)), body="Keeper Arm", force=True)
            to = r.choice([p for p in self.tm.onpitch if p.group in ("MF", "FW")])
            end = (r.uniform(55, 80), r.uniform(15, 65))
            return self.pass_to(to, end, height="High Pass", body="Drop Kick", acc=0.5)
        if self.scene and kind in ("corner", "free_kick") and self.scene.get("how") != kind:
            # a scripted spell that starts from a set piece: play it short and keep the script going
            if kind == "corner":
                self.ball = (119.9, r.choice([0.1, 79.9]))
            ptype = "Corner" if kind == "corner" else "Free Kick"
            return self.pass_to(*self.pick_recipient(max_len=25), ptype=ptype, force=True)
        if kind == "corner":
            return self.corner()
        if kind == "free_kick":
            return self.free_kick()
        if kind == "penalty":
            return self.penalty()
        return None

    def corner(self) -> Start | None:
        sim, r = self.sim, self.rng
        scripted = bool(self.scene and self.scene.get("how") == "corner")
        self.ball = (119.9, r.choice([0.1, 79.9]))
        self.carrier = r.choice([p for p in self.tm.onpitch if p.group in ("MF", "FW")])
        if scripted:
            target = sim.find(self.team, self.scene.get("scorer") or self.scene.get("shooter"), ("DF",))
        else:
            target = r.choice([p for p in self.tm.onpitch if p.group in ("DF", "FW") and p is not self.carrier])
        end = (r.uniform(105, 114), r.uniform(32, 48))
        tech = r.choice(["Inswinging", "Outswinging"])
        return self.pass_to(target, end, ptype="Corner", height="High Pass", acc=0.33, technique=tech,
                            force=scripted, then_header=True,
                            header_forced=self.scene_outcome() if scripted else None)

    def free_kick(self) -> Start | None:
        sim, r = self.sim, self.rng
        x, y = self.ball
        dist = math.hypot(120 - x, 40 - y)
        if self.scene and self.scene.get("how") == "free_kick":
            self.carrier = sim.find(self.team, self.scene.get("scorer"), ("FW", "MF"))
            return self.shoot(shot_type="Free Kick", forced=self.scene_outcome())
        if x > 88 and dist < 32 and r.random() < 0.35:
            return self.shoot(shot_type="Free Kick")
        if x > 85:
            target = r.choice([p for p in self.tm.onpitch if p.group in ("DF", "FW")])
            end = (r.uniform(104, 114), r.uniform(30, 50))
            return self.pass_to(target, end, ptype="Free Kick", height="High Pass", acc=0.35, then_header=True)
        if x < 45 and r.random() < 0.45:
            to = r.choice([p for p in self.tm.onpitch if p.group in ("MF", "FW")])
            end = (r.uniform(65, 95), r.uniform(10, 70))
            return self.pass_to(to, end, ptype="Free Kick", height="High Pass", acc=0.5)
        return self.pass_to(*self.pick_recipient(max_len=25), ptype="Free Kick", acc=0.95)

    def penalty(self) -> Start | None:
        sim = self.sim
        self.ball = (108.0, 40.0)
        name = self.scene.get("scorer") if self.scene else None
        self.carrier = sim.find(self.team, name, ("FW",))
        return self.shoot(shot_type="Penalty", forced=self.scene_outcome())

    def scene_outcome(self) -> str | None:
        if not self.scene:
            return None
        return "Goal" if self.scene["kind"] == "goal" else self.scene.get("outcome", "Saved")

    # ------------------------------------------------------------ one touch
    def touch(self) -> Start | None:
        sim, r = self.sim, self.rng
        mode, x = self.mode, self.ball[0]
        forced = mode in ("dull", "scripted")
        self.pressure = False

        # carry
        p_carry = 0.4 if self.carrier.group == "GK" else 0.78
        if r.random() < p_carry:
            self.carry()

        # pressure from the defending side
        p_press = {"dull": 0.05, "circulate": 0.12, "build": 0.2, "attack": 0.34, "counter": 0.3,
                   "scripted": 0.25}[mode] * self.sim.teams[self.opp].style["press"] / 0.25
        if r.random() < p_press:
            self.press()

        if mode == "scripted":
            return self.scripted_step()

        if not forced:
            foul_p = {"circulate": 0.01, "build": 0.022, "attack": 0.05, "counter": 0.05}[mode]
            foul_p *= sim.teams[self.opp].style["foul"]
            red = sim.due_red(self.opp)
            if red or r.random() < foul_p:
                return self.foul(red_scene=red)
            wide_back = self.carrier.pos_id in (2, 6, 7, 8)
            if (self.carrier.group in ("FW", "MF") or wide_back) and x > 45 and mode in ("attack", "build", "counter"):
                if r.random() < 0.13 * self.style["dribble"]:
                    nxt = self.dribble()
                    if nxt:
                        return nxt
            if self.carrier.group == "DF" and x < 22 and self.pressure and r.random() < 0.4:
                return self.own_clearance()
            lose = {"circulate": 0.012, "build": 0.03, "attack": 0.065, "counter": 0.06}[mode]
            if self.pressure:
                lose *= 1.8
            if r.random() < lose:
                return self.lose_ball()
            dist = math.hypot(120 - x, 40 - self.ball[1])
            if self.carrier.group == "MF" and 84 <= x < 96 and 22 < self.ball[1] < 58 and r.random() < 0.08:
                return self.shoot()  # speculative long shot
            if x >= 92 and dist <= 30:
                p_shot = clip(0.06 + 2.8 * xg(self.ball), 0, 0.65)
                if self.carrier.group == "FW":
                    p_shot *= 0.5
                if mode == "counter":
                    p_shot *= 1.3
                if r.random() < p_shot:
                    return self.shoot()
        return self.normal_pass()

    # ------------------------------------------------------------ actions
    def carry(self) -> None:
        sim, r = self.sim, self.rng
        x, y = self.ball
        if self.mode == "dull":
            dx, dy = r.uniform(-3, 3), r.uniform(-4, 4)
        elif self.mode == "circulate":
            dx, dy = r.uniform(-3, 5), r.uniform(-5, 5)
        elif self.mode in ("attack", "scripted"):
            dx, dy = r.uniform(1, 10), (40 - y) * r.uniform(0, 0.25) + r.uniform(-3, 3)
        elif self.mode == "counter":
            dx, dy = r.uniform(8, 22), r.uniform(-6, 6)
        else:
            dx, dy = r.uniform(0, 9), r.uniform(-5, 5)
        if self.carrier.group == "GK":
            dx, dy = r.uniform(0, 4), r.uniform(-3, 3)
        end = (clip(x + dx, 1, 117), clip(y + dy, 1, 79))
        if self.mode == "dull":
            end = (soft_band(end[0], 42, 78, r), end[1])
        dist = math.hypot(end[0] - x, end[1] - y)
        dur = 0.5 + dist / 5.2 + r.uniform(0, 1.0)
        sim.tick(r.uniform(0.1, 0.4))
        e = sim.emit("Carry", self.team, self.carrier, self.ball, dur, sub={"end_location": [r1(end[0]), r1(end[1])]})
        if self.prev_evt:
            sim.link(self.prev_evt, e)
        self.prev_evt = e
        sim.tick(dur)
        self.ball = end

    def press(self) -> None:
        sim, r = self.sim, self.rng
        d, dloc = sim.nearest_def(self.opp, self.ball, spread=5)
        bd = flip(self.ball)
        loc = (bd[0] + r.uniform(-2, 2), bd[1] + r.uniform(-2, 2))
        e = sim.emit("Pressure", self.opp, d, loc, r.uniform(0.4, 1.6), t=sim.t - r.uniform(0, 0.4))
        if self.prev_evt:
            sim.link(self.prev_evt, e)
        self.pressure = True

    def pick_recipient(self, max_len: float = 45.0, progressive: bool = False) -> tuple[Player, tuple]:
        sim, r = self.sim, self.rng
        bx, by = self.ball
        mode = self.mode
        cands, weights, locs = [], [], []
        for p in self.tm.onpitch:
            if p is self.carrier:
                continue
            px, py = sim.att_pos(p, self.ball)
            jitter = 1.0 if p.group == "GK" else 3.0
            px += r.gauss(0, jitter)
            py += r.gauss(0, jitter)
            if mode == "dull":
                px = soft_band(px, 42, 78, r)
            dx = px - bx
            dist = math.hypot(dx, py - by)
            if dist > max_len or dist < 4:
                continue
            role = {"GK": 0.45, "DF": 1.2, "MF": 0.95, "FW": 0.9}[p.group]
            if mode in ("dull", "circulate"):
                w = math.exp(-(((dist - 15) / 9) ** 2)) * (1.0 if 38 <= px <= 82 else 0.15)
                w *= 1.2 if dx < 6 else 0.55
                if p.group == "GK":
                    w *= 0.1 if mode == "dull" else 0.5
            elif mode == "build":
                w = math.exp(-(((dist - 18) / 11) ** 2)) * (1 + 0.04 * max(dx, 0))
                if p.group == "GK" and bx < 45:
                    w *= 3.0
            else:  # attack, counter, scripted
                w = math.exp(-(((dist - 16) / 11) ** 2)) * (1 + 0.08 * max(dx, 0))
                if p.group == "FW" and px > 92:
                    w *= 1.8
                if mode == "counter":
                    w *= 1 + 0.12 * max(dx, 0)
            if progressive:
                w *= 4.0 if dx > 12 else 0.05
            cands.append(p)
            weights.append(w * role + 1e-6)
            locs.append((px, py))
        if not cands:  # fall back to anyone
            p = self.rng.choice([q for q in self.tm.onpitch if q is not self.carrier])
            return p, sim.att_pos(p, self.ball)
        i = r.choices(range(len(cands)), weights=weights)[0]
        return cands[i], (clip(locs[i][0], 1, 119), clip(locs[i][1], 1, 79))

    def normal_pass(self) -> Start | None:
        r, x, y = self.rng, *self.ball
        mode = self.mode
        if mode == "dull":
            return self.pass_to(*self.pick_recipient(max_len=30), force=True)
        if mode == "circulate" and r.random() < self.style["progress"]:
            to, end = self.pick_recipient(progressive=True)
            return self.pass_to(to, end)
        if mode == "build" and x < 45 and r.random() < self.style["direct"] * 0.5:
            to = r.choice([p for p in self.tm.onpitch if p.group in ("FW", "MF")])
            end = (r.uniform(60, 92), r.uniform(10, 70))
            return self.pass_to(to, end, height="High Pass")
        if mode in ("attack", "counter") and x > 92 and (y < 18 or y > 62) and r.random() < 0.35:
            target = r.choice([p for p in self.tm.onpitch if p.group in ("FW", "MF")])
            end = (r.uniform(104, 114), r.uniform(30, 50))
            return self.pass_to(target, end, height="High Pass", cross=True)
        to, end = self.pick_recipient()
        through = mode in ("attack", "counter") and to.group == "FW" and end[0] - x > 15 and r.random() < 0.3
        return self.pass_to(to, end, through=through)

    def pass_to(self, to: Player, end, ptype: str | None = None, height: str | None = None,
                body: str | None = "auto", acc: float | None = None, force: bool = False,
                cross: bool = False, through: bool = False, technique: str | None = None,
                then_header: bool = False, header_forced: str | None = None) -> Start | None:
        sim, r = self.sim, self.rng
        start = self.ball
        dx, dy = end[0] - start[0], end[1] - start[1]
        length = math.hypot(dx, dy)
        if height is None:
            height = "Ground Pass" if length < 25 else r.choice(["Low Pass", "High Pass"])
        if body == "auto":
            body = "Head" if r.random() < 0.04 else ("Left Foot" if r.random() < 0.32 else "Right Foot")
        speed = 13.0 if height == "Ground Pass" else 17.0
        dur = 0.25 + length / speed + (0.5 if height == "High Pass" else 0)
        # completion
        if acc is None:
            acc = self.style["pass_acc"]
            if self.mode == "circulate":
                acc += 0.05
            if length > 35:
                acc -= 0.25
            if cross:
                acc = 0.3
            elif end[0] > 102:
                acc -= 0.25
            elif start[0] > 80:
                acc -= 0.06
            if dx < -3:
                acc += 0.08
            if self.pressure:
                acc -= 0.08
            acc = clip(acc, 0.15, 0.98)
        force = force or self.mode in ("dull", "scripted")  # scripted spells never lose the ball
        complete = force or r.random() < acc
        offside = (not force and through and r.random() < 0.15)
        if offside:
            complete = False

        sim.tick(r.uniform(0.2, 0.9) if self.prev_evt else r.uniform(0.4, 1.2))
        p = {"recipient": {"id": to.pid, "name": to.name}, "length": round(length, 6),
             "angle": round(math.atan2(dy, dx), 7), "height": named(PASS_HEIGHT, height),
             "end_location": [r1(clip(end[0], 0, 120)), r1(clip(end[1], 0, 80))]}
        if body:
            p["body_part"] = named(BODY, body)
        if ptype is None and self.passes == 0 and self.st.via:
            ptype = self.st.via
        if ptype:
            p["type"] = named(PASS_TYPE, ptype)
        if cross:
            p["cross"] = True
        if abs(dy) > 35 and dx > -5:
            p["switch"] = True
        if through:
            p["technique"] = {"id": 108, "name": "Through Ball"}
            p["through_ball"] = True
        if technique:
            p["technique"] = {"id": {"Inswinging": 104, "Outswinging": 105}[technique], "name": technique}
            p[technique.lower()] = True

        out_of_play = False
        if not complete:
            if offside:
                p["outcome"] = named(PASS_OUTCOME, "Pass Offside")
            elif not (cross or then_header) and r.random() < 0.13:
                p["outcome"] = named(PASS_OUTCOME, "Out")
                out_of_play = True
                if end[0] < 118:  # over the touchline: the ball ends on it
                    end = (end[0] + dx * 0.15, 0.0 if dy < 0 or (dy == 0 and end[1] < 40) else 80.0)
                p["end_location"] = [r1(clip(end[0], 0, 120)), r1(end[1])]
            else:
                p["outcome"] = named(PASS_OUTCOME, "Incomplete")
        e = sim.emit("Pass", self.team, self.carrier, start, dur, sub=p, pressure=self.pressure, out=out_of_play)
        if self.prev_evt:
            sim.link(self.prev_evt, e)
        sim.last_pass = e
        self.passes += 1
        if sim.seg:
            sim.seg["passes"] += 1
        sim.tick(dur)

        if complete:
            rec = sim.emit("Ball Receipt*", self.team, to, end)
            sim.link(e, rec)
            self.prev_evt = rec
            self.carrier, self.ball = to, (clip(end[0], 0.5, 119.5), clip(end[1], 0.5, 79.5))
            if height == "High Pass" and length > 30 and r.random() < 0.6:
                d, dloc = sim.nearest_def(self.opp, self.ball)
                sim.emit("Duel", self.opp, d, flip(self.ball), sub={"type": named(DUEL_TYPE, "Aerial Lost")})
                p["aerial_won"] = True
            if then_header:
                return self.header_shot(header_forced)
            return None

        # ---- incomplete
        if height == "High Pass" and not out_of_play and r.random() < 0.5:
            sim.emit("Duel", self.team, to, end, sub={"type": named(DUEL_TYPE, "Aerial Lost")})
        if not offside and not out_of_play and r.random() < 0.9:
            rec = sim.emit("Ball Receipt*", self.team, to, end, sub={"outcome": {"id": 9, "name": "Incomplete"}})
            sim.link(e, rec)
        self.st.via = None
        if offside:
            sim.seg_close("offside")
            sim.tick(r.uniform(12, 22))
            d, _ = sim.nearest_def(self.opp, end)
            return Start(self.opp, d, flip(end), restart="free_kick", pattern="From Free Kick")
        if out_of_play:
            sim.seg_close("out")
            sim.tick(r.uniform(6, 15))
            if end[0] >= 118:
                sim.tick(r.uniform(8, 14))
                return Start(self.opp, sim.gk(self.opp), (6, 40), restart="goal_kick", pattern="From Goal Kick")
            side = 0.1 if end[1] < 40 else 79.9
            loc = flip((clip(end[0], 1, 119), side))
            thrower = sim.nearest_def(self.opp, (end[0], side))[0]
            return Start(self.opp, thrower, loc, restart="throw_in", pattern="From Throw In")
        if end[0] > 100 and (cross or then_header or height == "High Pass"):
            return self.defend_box_ball(end)
        if end[0] > 88 and r.random() < 0.45:
            return self.defend_box_ball(end, keeper=False)
        if length < 30 and r.random() < 0.25:
            mid = (start[0] + dx * r.uniform(0.3, 0.8), start[1] + dy * r.uniform(0.3, 0.8))
            d, _ = sim.nearest_def(self.opp, mid, spread=4)
            sim.seg_close("turnover")
            self.sim.possession += 1
            self.sim.poss_team = self.opp
            self.sim.pattern = "Regular Play"
            ev = sim.emit("Interception", self.opp, d, flip(mid), sub={"outcome": named(DUEL_OUTCOME, "Won")})
            sim.link(e, ev)
            return self.turnover_start(d, flip(mid), "Interception", ev)
        if r.random() < 0.2:
            d, _ = sim.nearest_def(self.opp, start, spread=4)
            sim.emit("Block", self.opp, d, flip((start[0] + dx * 0.15, start[1] + dy * 0.15)), related=[e["id"]])
        d, _ = sim.nearest_def(self.opp, end, spread=6)
        return self.recovery(d, flip(end), e)

    def recovery(self, d: Player, loc, related: dict | None) -> Start:
        sim = self.sim
        sim.seg_close("turnover")
        sim.possession += 1
        sim.poss_team = d.team_idx
        sim.pattern = "Regular Play"
        sim.tick(self.rng.uniform(0.3, 1.2))
        ev = sim.emit("Ball Recovery", d.team_idx, d, loc)
        if related:
            sim.link(related, ev)
        return self.turnover_start(d, loc, "Recovery", ev)

    def turnover_start(self, d: Player, loc, via: str, ev: dict) -> Start:
        """The caller already opened the new possession (team changed), so play() won't count it again."""
        sim, r = self.sim, self.rng
        team = d.team_idx
        mode = "build"
        pattern = "Regular Play"
        if loc[0] > 35 and r.random() < sim.teams[team].style["counter"] * 0.6:
            mode = "counter"
            if r.random() < 0.3:
                pattern = "From Counter"
        st = Start(team, d, loc, pattern=pattern, mode=mode, via=via)
        st.prev = ev
        return st

    def defend_box_ball(self, end, keeper: bool = True) -> Start:
        """A ball into the final third was not completed: keeper claims or a defender clears."""
        sim, r = self.sim, self.rng
        sim.seg_close("cleared")
        if keeper and r.random() < 0.22:
            g = sim.gk(self.opp)
            sim.possession += 1
            sim.poss_team = self.opp
            sim.emit("Goal Keeper", self.opp, g, flip(end),
                     sub={"type": named(GK_TYPE, "Collected"), "outcome": named(GK_OUTCOME, "Success")})
            sim.tick(r.uniform(3, 8))
            return Start(self.opp, g, flip(end), restart="keeper",
                         pattern="From Keeper" if r.random() < 0.3 else "Regular Play")
        d, _ = sim.nearest_def(self.opp, end, spread=4)
        if d.group != "DF" and r.random() < 0.8:
            d = r.choice([q for q in sim.teams[self.opp].onpitch if q.group == "DF"])
        head = r.random() < 0.55
        land = (r.uniform(35, 60), r.uniform(5, 75))  # in defending team's frame
        out = r.random() < 0.25
        sim.emit("Clearance", self.opp, d, flip(end), pressure=r.random() < 0.6, out=out,
                 sub={"body_part": named(BODY, "Head" if head else "Right Foot"), **({"head": True} if head else {"right_foot": True})})
        if out:
            sim.tick(r.uniform(8, 16))
            if r.random() < 0.4:
                return Start(self.team, self.carrier, (119.9, 0.1), restart="corner", pattern="From Corner")
            side = 0.1 if land[1] < 40 else 79.9
            thrower = self.rng.choice([p for p in self.tm.onpitch if p.group != "GK"])
            return Start(self.team, thrower, flip((land[0], side)), restart="throw_in", pattern="From Throw In")
        sim.tick(r.uniform(1.0, 2.5))
        if r.random() < 0.55:
            d2, _ = sim.nearest_def(self.opp, flip(land), spread=10)
            return self.recovery(d2, land, None)
        # attacking team wins the second ball
        p = self.rng.choice([q for q in self.tm.onpitch if q.group in ("MF", "DF")])
        loc = flip(land)
        self.prev_evt = sim.emit("Ball Recovery", self.team, p, loc)
        self.carrier, self.ball, self.mode = p, loc, "build"
        return None  # same possession carries on

    def own_clearance(self) -> Start | None:
        """A defender under pressure near his own goal boots it clear."""
        sim, r = self.sim, self.rng
        sim.seg_close("cleared")
        head = r.random() < 0.3
        out = r.random() < 0.3
        sim.emit("Clearance", self.team, self.carrier, self.ball, pressure=True, out=out,
                 sub={"body_part": named(BODY, "Head" if head else "Right Foot"), **({"head": True} if head else {"right_foot": True})})
        land = (r.uniform(50, 80), r.uniform(5, 75))
        sim.tick(r.uniform(1.0, 2.5))
        if out:
            sim.tick(r.uniform(6, 14))
            side = 0.1 if land[1] < 40 else 79.9
            thrower = sim.nearest_def(self.opp, (land[0], side))[0]
            return Start(self.opp, thrower, flip((land[0], side)), restart="throw_in", pattern="From Throw In")
        if r.random() < 0.55:
            d, _ = sim.nearest_def(self.opp, land, spread=10)
            return self.recovery(d, flip(land), None)
        p = r.choice([q for q in self.tm.onpitch if q.group in ("MF", "FW")])
        self.prev_evt = sim.emit("Ball Recovery", self.team, p, land)
        self.carrier, self.ball, self.mode = p, land, "build"
        return None

    def lose_ball(self) -> Start:
        sim, r = self.sim, self.rng
        d, dloc = sim.nearest_def(self.opp, self.ball, spread=4)
        if r.random() < 0.5:
            ev = sim.emit("Miscontrol", self.team, self.carrier, self.ball, pressure=self.pressure)
            if r.random() < 0.35:  # a teammate picks up the loose ball
                mate, _ = self.pick_recipient(max_len=15)
                mloc = (clip(self.ball[0] + r.uniform(-3, 3), 1, 119), clip(self.ball[1] + r.uniform(-3, 3), 1, 79))
                self.sim.tick(r.uniform(0.5, 1.5))
                self.prev_evt = sim.emit("Ball Recovery", self.team, mate, mloc)
                self.carrier, self.ball = mate, mloc
                return None
        else:
            duel = sim.emit("Duel", self.opp, d, flip(self.ball),
                            sub={"type": named(DUEL_TYPE, "Tackle"), "outcome": named(DUEL_OUTCOME, r.choice(["Won", "Success In Play"]))})
            ev = sim.emit("Dispossessed", self.team, self.carrier, self.ball, pressure=True)
            sim.link(duel, ev)
        return self.recovery(d, flip(self.ball), ev)

    def dribble(self) -> Start | None:
        sim, r = self.sim, self.rng
        d, _ = sim.nearest_def(self.opp, self.ball, spread=4)
        ok = r.random() < 0.6
        e = sim.emit("Dribble", self.team, self.carrier, self.ball, pressure=True,
                     sub={"outcome": {"id": 8, "name": "Complete"} if ok else {"id": 9, "name": "Incomplete"}})
        if ok:
            past = sim.emit("Dribbled Past", self.opp, d, flip(self.ball))
            sim.link(e, past)
            self.prev_evt = e
            self.ball = (clip(self.ball[0] + r.uniform(3, 8), 0, 117), self.ball[1])
            return None
        duel = sim.emit("Duel", self.opp, d, flip(self.ball),
                        sub={"type": named(DUEL_TYPE, "Tackle"), "outcome": named(DUEL_OUTCOME, "Won")})
        sim.link(e, duel)
        return self.recovery(d, flip(self.ball), duel)

    def foul(self, red_scene: dict | None = None, in_box: bool = False) -> Start:
        sim, r = self.sim, self.rng
        if red_scene:
            fouler = sim.find(self.opp, red_scene.get("player"), ("DF", "MF"))
        else:
            fouler, _ = sim.nearest_def(self.opp, self.ball, spread=4)
        loc = self.ball
        if not in_box and loc[0] > 102 and 18 < loc[1] < 62:
            loc = (r.uniform(96, 101), loc[1])  # unscripted fouls stay outside the box
        sim.seg_close("foul")
        fc = {}
        if in_box:
            fc["penalty"] = True
        card = None
        if red_scene:
            card = "Red Card"
            red_scene["done"] = True
        elif r.random() < 0.13 * sim.teams[self.opp].style["foul"]:
            card = "Second Yellow" if fouler.yellow else "Yellow Card"
        if card:
            fc["card"] = named(CARD, card)
        fcev = sim.emit("Foul Committed", self.opp, fouler, flip(loc), sub=fc or None)
        fw = sim.emit("Foul Won", self.team, self.carrier, loc, pressure=True, sub={"penalty": True} if in_box else None)
        sim.link(fcev, fw)
        if card:
            m, s = sim.minute_second()
            fouler.cards.append({"time": mmss(m, s), "card_type": card, "reason": "Foul Committed", "period": sim.period})
            if card == "Yellow Card":
                fouler.yellow = True
            else:
                sim.send_off(fouler, "Foul Committed (Second Yellow)" if card == "Second Yellow" else "Foul Committed (Red Card)")
            sim.tick(r.uniform(20, 35))
        if in_box:
            sim.tick(r.uniform(60, 95))
            return Start(self.team, self.carrier, (108.0, 40.0), restart="penalty", pattern="Other", scene=self.scene)
        sim.tick(r.uniform(12, 30))
        keep = self.scene if self.scene and self.scene.get("how") == "free_kick" else None
        return Start(self.team, self.carrier, loc, restart="free_kick", pattern="From Free Kick", scene=keep)

    # ------------------------------------------------------------ shots
    def shoot(self, shot_type: str = "Open Play", forced: str | None = None, header: bool = False,
              block_out: bool = False) -> Start:
        sim, r = self.sim, self.rng
        loc = self.ball
        val = 0.7835 if shot_type == "Penalty" else xg(loc, header)
        if forced:
            outcome = forced
        else:
            on_target = clip(0.25 + 1.2 * val, 0.25, 0.7)
            roll = r.random()
            if roll < 0.32:
                outcome = "Blocked"
            elif roll < 0.32 + on_target * 0.68:
                outcome = "Saved"
            elif roll < 0.97:
                outcome = r.choice(["Off T", "Off T", "Off T", "Wayward"])
            else:
                outcome = "Post"
        gy = r.uniform(36.6, 43.4)
        if outcome in ("Goal", "Saved"):
            end = [120.0, round(gy, 1), round(r.uniform(0.2, 2.4), 1)]
        elif outcome == "Post":
            end = [120.0, r.choice([36.0, 44.0]), round(r.uniform(0.3, 2.4), 1)]
        elif outcome == "Off T":
            end = [120.0, round(r.choice([r.uniform(30, 35.8), r.uniform(44.2, 50)]), 1), round(r.uniform(0.2, 4), 1)]
        elif outcome == "Wayward":
            end = [round(r.uniform(105, 118), 1), round(r.uniform(20, 60), 1)]
        else:
            end = [round(min(loc[0] + r.uniform(1, 4), 119.5), 1), round(clip(loc[1] + r.uniform(-2, 2), 0.5, 79.5), 1)]
        shot = {"statsbomb_xg": round(val, 8), "end_location": end,
                "type": named(SHOT_TYPE, shot_type), "outcome": named(SHOT_OUTCOME, outcome),
                "technique": named(TECH, "Normal" if header or r.random() < 0.88 else r.choice(["Volley", "Half Volley"])),
                "body_part": named(BODY, "Head" if header else ("Left Foot" if r.random() < 0.33 else "Right Foot")),
                "freeze_frame": self.freeze_frame(loc)}
        lp = sim.last_pass
        if shot_type == "Open Play" and lp and lp["team"]["id"] == self.tm.info["team_id"] and lp["possession"] == sim.possession \
                and "outcome" not in lp["pass"]:
            shot["key_pass_id"] = lp["id"]
        sim.tick(r.uniform(0.2, 0.8))
        se = sim.emit("Shot", self.team, self.carrier, loc, r.uniform(0.3, 1.2), sub=shot, pressure=self.pressure,
                      out=outcome in ("Off T", "Wayward"))
        if "key_pass_id" in shot:
            lp["pass"]["shot_assist"] = True
            lp["pass"]["assisted_shot_id"] = se["id"]
            if outcome == "Goal":
                lp["pass"].pop("shot_assist")
                lp["pass"]["goal_assist"] = True
        g = sim.gk(self.opp)
        gloc = (round(r.uniform(1, 4), 1), round(clip(80 - loc[1], 36, 44), 1))
        sim.tick(r.uniform(0.3, 0.7))
        sim.seg_close("goal" if outcome == "Goal" else "shot")
        if shot_type == "Penalty":
            gtype = "Penalty Conceded" if outcome == "Goal" else "Penalty Saved"
            ge = sim.emit("Goal Keeper", self.opp, g, gloc, sub={"type": named(GK_TYPE, gtype),
                          **({"outcome": named(GK_OUTCOME, "Success")} if outcome != "Goal" else {})})
            sim.link(se, ge)
        elif outcome == "Goal":
            ge = sim.emit("Goal Keeper", self.opp, g, gloc, sub={"type": named(GK_TYPE, "Goal Conceded"),
                          "outcome": named(GK_OUTCOME, "No Touch"), "position": {"id": 44, "name": "Set"}})
            sim.link(se, ge)
        elif outcome == "Saved":
            ge = sim.emit("Goal Keeper", self.opp, g, gloc, sub={
                "type": named(GK_TYPE, "Shot Saved"), "position": {"id": 44, "name": "Set"},
                "technique": {"id": 45, "name": "Diving"} if r.random() < 0.5 else {"id": 46, "name": "Standing"},
                "outcome": named(GK_OUTCOME, r.choice(["Success", "In Play Safe", "In Play Danger", "Touched Out"]))})
            sim.link(se, ge)
        else:  # Off T, Post, Wayward, Blocked: the keeper still "faced" the shot
            ge = sim.emit("Goal Keeper", self.opp, g, gloc, sub={"type": named(GK_TYPE, "Shot Faced"),
                          "position": {"id": 44, "name": "Set"}})
            sim.link(se, ge)

        # what happens next
        if outcome == "Goal":
            sim.score[self.team] += 1
            sim.tick(r.uniform(55, 85))
            return sim.kickoff_start(self.opp)
        if outcome == "Saved":
            ge_out = ge["goalkeeper"]["outcome"]["name"]
            if ge_out == "Touched Out":
                sim.tick(r.uniform(18, 30))
                return Start(self.team, self.carrier, (119.9, 0.1), restart="corner", pattern="From Corner")
            if ge_out in ("Success", "In Play Safe") or shot_type == "Penalty":
                if shot_type == "Penalty" and r.random() < 0.5:
                    sim.tick(r.uniform(15, 25))
                    return Start(self.team, self.carrier, (119.9, 0.1), restart="corner", pattern="From Corner")
                sim.tick(r.uniform(3, 8))
                return Start(self.opp, g, (5.0, 40.0), restart="keeper", pattern="Regular Play")
            d, _ = sim.nearest_def(self.opp, (112, 40))
            return self.recovery(d, (r.uniform(5, 14), r.uniform(28, 52)), ge)
        if outcome == "Blocked":
            d, _ = sim.nearest_def(self.opp, loc, spread=3)
            out = block_out or r.random() < 0.35
            be = sim.emit("Block", self.opp, d, flip((end[0], end[1])), out=out)
            sim.link(se, be)
            if out:
                sim.tick(r.uniform(18, 30))
                return Start(self.team, self.carrier, (119.9, 0.1), restart="corner", pattern="From Corner",
                             scene=self.scene if block_out else None)
            return self.recovery(d, flip((end[0], end[1])), be)
        sim.tick(r.uniform(15, 28))
        return Start(self.opp, g, (6, 40), restart="goal_kick", pattern="From Goal Kick")

    def header_shot(self, forced: str | None) -> Start | None:
        """After a completed cross / corner: header at goal, or the ball is knocked down and play goes on."""
        if forced or self.rng.random() < 0.55:
            return self.shoot(header=True, forced=forced)
        return None

    def freeze_frame(self, loc) -> list:
        sim = self.sim
        out = []
        for p in self.tm.onpitch:
            if p is self.carrier:
                continue
            px, py = sim.att_pos(p, loc)
            if px >= loc[0] - 20:
                out.append({"location": [round(px + self.rng.gauss(0, 2), 1), round(py + self.rng.gauss(0, 2), 1)],
                            "player": {"id": p.pid, "name": p.name},
                            "position": {"id": p.pos_id, "name": POSITIONS[p.pos_id][0]}, "teammate": True})
        bd = flip(loc)
        for p in sim.teams[self.opp].onpitch:
            dx, dy = sim.def_pos(p, bd)
            ax, ay = flip((dx, dy))
            if ax >= loc[0] - 20 or p.group == "GK":
                if p.group == "GK":
                    ax, ay = 120 - self.rng.uniform(1, 4), clip(40 + (loc[1] - 40) * 0.15, 37, 43)
                out.append({"location": [round(ax + self.rng.gauss(0, 1.5), 1), round(ay + self.rng.gauss(0, 1.5), 1)],
                            "player": {"id": p.pid, "name": p.name},
                            "position": {"id": p.pos_id, "name": POSITIONS[p.pos_id][0]}, "teammate": False})
        return out

    # ------------------------------------------------------------ scripted possessions
    def scripted_step(self) -> Start | None:
        """Drive a scripted goal / chance / penalty: progress quickly, then finish."""
        sc, r, sim = self.scene, self.rng, self.sim
        how = sc.get("how", "buildup")
        x, y = self.ball
        finisher_name = sc.get("scorer") or sc.get("shooter")
        # finishing conditions
        if how in ("buildup", "counter") and x >= 100 and 26 < y < 54:
            scorer = sim.find(self.team, finisher_name, ("FW",))
            if self.carrier is not scorer and self.passes < 12:
                end = (r.uniform(104, 110), r.uniform(33, 47))
                return self.pass_to(scorer, end, force=True)
            return self.shoot(forced=self.scene_outcome())
        if how == "cross" and x >= 96 and (y < 20 or y > 60):
            scorer = sim.find(self.team, finisher_name, ("FW",))
            end = (r.uniform(106, 112), r.uniform(35, 45))
            return self.pass_to(scorer, end, height="High Pass", cross=True, force=True,
                                then_header=True, header_forced=self.scene_outcome())
        if how == "corner" and x >= 96:
            return self.shoot(forced="Blocked", block_out=True)  # the corner restart carries the scene on
        if how in ("penalty", "free_kick") and x >= 96:
            if how == "penalty":
                self.ball = (r.uniform(104, 112), r.uniform(30, 50))
            else:
                self.ball = (r.uniform(92, 96), r.uniform(34, 46))
            return self.foul(in_box=how == "penalty")
        # otherwise progress the ball
        if how == "cross":
            wing = 8 if y < 40 else 72
            to = sim.find(self.team, None, ("FW", "MF", "DF"))
            end = (clip(x + r.uniform(12, 25), 0, 104), wing + r.uniform(-4, 4))
            return self.pass_to(to, end, force=True)
        to, end = self.pick_recipient(progressive=True)
        if end[0] <= x:
            end = (clip(x + r.uniform(10, 22), 0, 108), clip(end[1] * 0.6 + 16, 4, 76))
        if self.mode == "scripted" and how != "counter" and self.passes < 2 and x < 75:
            end = (clip(x + r.uniform(6, 14), 0, 105), end[1])
        return self.pass_to(to, end, force=True)
