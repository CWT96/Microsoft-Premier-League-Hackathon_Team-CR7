"""timing_sim.py — replays a match through detector, timing agent and simulated users' pacers.

Last Modified: 2026-10-24 23:50, first version from SPEC section 4.11
Inputs:  a loaded Match (data/source/<folder>, root overridable by TIMING_SIM_DATA_ROOT)
Outputs: SimReport in memory; data/generated/timing_report.md via write_report / the CLI

Workflow:
  - Detector.run(match) -> TimingAgent(RulePolicy()).step, one state per match second
  - four simulated users (fast, normal, hesitant, drifting) each run a Pacer, mirroring the app:
    attack start and the kick locks cards early (locked_ids / lock_now), stacks and water-break
    cards lock after 60 / 30 real seconds, the lobby and half time pause the match clock
  - metrics follow SPEC 4.11; typical_cards() = TYPICAL_ENGAGEMENT x normal user's answers

CAUTION: users are deterministic given ``seed`` (string-seeded random.Random per user).
Misses against the targets are reported, never tuned away here.
"""

from __future__ import annotations

import argparse
import os
import random
import statistics
from collections import Counter
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from backend.agents import timing_rules as R
from backend.agents.timing import RulePolicy, TimingAgent
from backend.core.constants import DEFAULT_MATCH_ID
from backend.core.types import DetectorState, Match, Moment
from backend.app.pacer import Pacer
from backend.detect.detector import Detector

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REPORT_PATH = REPO_ROOT / "data" / "generated" / "timing_report.md"
DATA_ROOT_ENV = "TIMING_SIM_DATA_ROOT"
DEFAULT_SEED = 20261024
NORMAL_USER = "normal"

# Real-time pauses while the match clock is frozen (SPEC 4.9, contracts/README.md).
LOBBY_PAUSE_S = 60
HALF_TIME_PAUSE_S = 60

# A typical non-fan plays along for about a quarter of the calm time, so n_typical is this
# share of what the always-engaged normal simulated user answers.
TYPICAL_ENGAGEMENT = 0.25

# Targets (SPEC 4.11 starting values).
TARGET_CALM_MOMENTS_PER_MIN = 12.0
TARGET_MEDIAN_WAIT_S = 2.0
TARGET_NORMAL_CARDS = 100
TARGET_CUT_SHORT_SHARE = 0.10


@dataclass(frozen=True)
class UserProfile:
    """How one simulated user behaves."""

    name: str
    answer_lo_s: float
    answer_hi_s: float
    result_view_s: int  # seconds between answering and being free for the next card
    answer_every: int = 1  # answers card k only if k % answer_every == 0 (drifting: 3)
    resume_s: int = 3  # seconds from nudge to tapping it


DEFAULT_USERS = (
    UserProfile("fast", 1.0, 2.0, 2),
    UserProfile(NORMAL_USER, 3.0, 4.0, 4),
    UserProfile("hesitant", 5.0, 6.0, 4),
    UserProfile("drifting", 3.0, 5.0, 3, answer_every=3, resume_s=20),
)


@dataclass
class UserStats:
    name: str
    seen: int = 0
    answered: int = 0
    ignored: int = 0  # locked by the clock without an answer
    cut_short: int = 0  # locked early by an attack while unanswered
    nudges: int = 0
    waits_s: list[int] = field(default_factory=list)  # calm-play waits for the next card


@dataclass(frozen=True)
class Target:
    name: str
    target: str
    actual: str
    passed: bool | None  # None = reported only


@dataclass
class SimReport:
    match_id: str
    seed: int
    live_seconds: int
    calm_seconds: int
    calm_moments: int
    moments: tuple[Moment, ...]
    fast_attack_moments: int
    post_kick_answers: int
    users: dict[str, UserStats]
    situation_by_user: dict[str, Counter]

    @property
    def calm_share(self) -> float:
        return self.calm_seconds / self.live_seconds if self.live_seconds else 0.0

    @property
    def calm_moments_per_min(self) -> float:
        return self.calm_moments / (self.calm_seconds / 60.0) if self.calm_seconds else 0.0

    @property
    def median_wait_s(self) -> float:
        waits = [w for u in self.users.values() for w in u.waits_s]
        return statistics.median(waits) if waits else float("nan")

    @property
    def cut_short_share(self) -> float:
        seen = sum(u.seen for u in self.users.values())
        return sum(u.cut_short for u in self.users.values()) / seen if seen else 0.0

    @property
    def moments_by_situation(self) -> Counter:
        return Counter(m.situation for m in self.moments)

    def targets(self) -> list[Target]:
        normal = self.users.get(NORMAL_USER)
        rows = [
            Target("Moments per minute in calm play", f">= {TARGET_CALM_MOMENTS_PER_MIN:g}",
                   f"{self.calm_moments_per_min:.1f}",
                   self.calm_moments_per_min >= TARGET_CALM_MOMENTS_PER_MIN),
            Target("Median wait for a free user in calm play", f"<= {TARGET_MEDIAN_WAIT_S:g} s",
                   f"{self.median_wait_s:.1f} s", self.median_wait_s <= TARGET_MEDIAN_WAIT_S),
        ]
        if normal is not None:
            rows.append(Target("Cards a normal user sees", f">= {TARGET_NORMAL_CARDS}",
                               f"{normal.seen} seen, {normal.answered} answered",
                               normal.seen >= TARGET_NORMAL_CARDS))
        rows += [
            Target("Moments opened during a fast attack", "0", str(self.fast_attack_moments),
                   self.fast_attack_moments == 0),
            Target("Set-piece cards answered after the kick", "0", str(self.post_kick_answers),
                   self.post_kick_answers == 0),
            Target("Open cards cut short by an attack", f"<= {TARGET_CUT_SHORT_SHARE:.0%}",
                   f"{self.cut_short_share:.1%}", self.cut_short_share <= TARGET_CUT_SHORT_SHARE),
            Target("Share of match time that is calm", "reported",
                   f"{self.calm_share:.1%}", None),
        ]
        return rows


# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
#
# Simulation
#
# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%


class _User:
    """One simulated user: a Pacer plus a behaviour profile."""

    def __init__(self, profile: UserProfile, seed: int) -> None:
        self.profile = profile
        self.pacer = Pacer(profile.name)
        self.rng = random.Random(f"{seed}-{profile.name}")
        self.stats = UserStats(profile.name)
        self.card: Moment | None = None
        self.answer_at: int | None = None  # tick at which the user answers the open card
        self.free_at = 0
        self.free_in_calm = False
        self.resume_at: int | None = None
        self.cards_shown = 0
        self.by_situation: Counter = Counter()


def _is_early_lock(m: Moment, state: DetectorState) -> str | None:
    """Reason an open instant locks early at this second, or None."""
    if state.attack_active or state.mode == "attack":
        return "attack"
    if m.lock_rule == "on_restart" and not state.restart_pending:
        return "restart"
    return None


def _time_locked(m: Moment, state: DetectorState) -> bool:
    return state.half > m.half or (state.half == m.half and state.t >= m.lock_at)


def simulate(match: Match, users: tuple[UserProfile, ...] = DEFAULT_USERS,
             seed: int = DEFAULT_SEED) -> SimReport:
    """Replay ``match`` through detector, timing and one pacer per simulated user."""
    detector = Detector(match.roster, match.roster.home, match.roster.away)
    agent = TimingAgent(RulePolicy(), match.match_id)
    sim_users = [_User(p, seed) for p in users]
    moments: list[Moment] = []
    open_moments: dict[str, Moment] = {}
    real_open: dict[str, int] = {}
    locked: set[str] = set()
    lock_tick: dict[str, int] = {}
    answers: list[tuple[str, int]] = []
    tick = 0
    live_s = calm_s = calm_moments = fast_attack = 0

    for state in detector.run(match):
        out = agent.step(state)
        is_live = state.mode in ("live", "attack")
        if is_live:
            live_s += 1
            calm_s += agent.calm
        for m in out.moments:
            moments.append(m)
            open_moments[m.moment_id] = m
            real_open[m.moment_id] = tick
            if m.kind in ("instant", "player_instant"):
                calm_moments += agent.calm
                fast_attack += state.attack_active or state.mode == "attack"
        repeat = {"lobby": LOBBY_PAUSE_S, "break": 1}.get(state.mode, 1)
        if state.mode == "break" and state.break_kind == "half_time":
            repeat = HALF_TIME_PAUSE_S
        for _ in range(repeat):
            _tick(state, tick, agent.calm, open_moments, real_open, locked, lock_tick,
                  answers, sim_users)
            tick += 1

    post_kick = sum(1 for qid, at in answers if qid in lock_tick and at >= lock_tick[qid]
                    and open_moments[qid].lock_rule == "on_restart")
    return SimReport(
        match_id=match.match_id, seed=seed, live_seconds=live_s, calm_seconds=calm_s,
        calm_moments=calm_moments, moments=tuple(moments), fast_attack_moments=fast_attack,
        post_kick_answers=post_kick, users={u.profile.name: u.stats for u in sim_users},
        situation_by_user={u.profile.name: u.by_situation for u in sim_users})


def _lock_moments(state, tick, open_moments, real_open, locked, lock_tick, users) -> None:
    """Lock moments the way the app does and tell every user's pacer."""
    for qid, m in list(open_moments.items()):
        if qid in locked:
            continue
        early = None
        timed = False
        if m.kind in ("instant", "player_instant"):
            early = _is_early_lock(m, state)
            timed = state.mode in ("live", "attack") and _time_locked(m, state)
            timed = timed or state.mode == "full_time"
        else:
            timed = tick - real_open[qid] >= m.lock_at - m.open_at
        if not (early or timed):
            continue
        locked.add(qid)
        lock_tick[qid] = tick
        for u in users:
            if u.card is not None and u.card.moment_id == qid:
                if early:
                    u.pacer.lock_now([qid])
                    u.stats.cut_short += early == "attack"
                else:
                    u.pacer.on_lock(qid, answered=False)
                    u.stats.ignored += 1
                u.card, u.answer_at = None, None
                u.free_at = tick
                u.free_in_calm = False


def _tick(state, tick, calm, open_moments, real_open, locked, lock_tick, answers, users) -> None:
    _lock_moments(state, tick, open_moments, real_open, locked, lock_tick, users)
    visible = [m for m in open_moments.values() if m.moment_id not in locked]
    for u in users:
        if u.card is not None and u.answer_at is not None and tick >= u.answer_at:
            u.pacer.on_answer(u.card.moment_id, state.t)
            answers.append((u.card.moment_id, tick))
            u.stats.answered += 1
            u.card, u.answer_at = None, None
            u.free_at = tick + u.profile.result_view_s
            u.free_in_calm = False
        if u.pacer.pending_nudge() is not None and u.resume_at is None:
            u.stats.nudges += 1
            u.resume_at = tick + u.profile.resume_s
        if u.resume_at is not None and tick >= u.resume_at:
            u.pacer.resume()
            u.resume_at = None
            u.free_at = tick
        if u.card is None and tick >= u.free_at:
            if tick == u.free_at:
                u.free_in_calm = calm
            m = u.pacer.next_card(state.t, state.half, visible, locked)
            if m is None:
                continue
            if u.free_in_calm:
                u.stats.waits_s.append(tick - u.free_at)
            u.cards_shown += 1
            u.stats.seen += 1
            u.by_situation[m.situation] += 1
            u.card = m
            if u.cards_shown % u.profile.answer_every == 0:
                latency = u.rng.uniform(u.profile.answer_lo_s, u.profile.answer_hi_s)
                u.answer_at = tick + max(1, round(latency))
            else:
                u.answer_at = None


# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
#
# Report, n_typical and CLI
#
# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%


def render_report(report: SimReport) -> str:
    """Markdown text of the timing report."""
    lines = [f"# Timing report: {report.match_id}", "",
             f"Seed {report.seed}. Live seconds {report.live_seconds}, calm seconds "
             f"{report.calm_seconds}, moments {len(report.moments)}.", "",
             "## Targets", "", "| Metric | Target | Actual | Status |", "|---|---|---|---|"]
    for t in report.targets():
        status = "reported" if t.passed is None else ("PASS" if t.passed else "MISS")
        lines.append(f"| {t.name} | {t.target} | {t.actual} | {status} |")
    lines += ["", "## Moments by situation", "", "| Situation | Moments | " + " | ".join(
        report.users) + " |", "|---|---|" + "---|" * len(report.users)]
    for sit, n in sorted(report.moments_by_situation.items(), key=lambda kv: -kv[1]):
        per_user = " | ".join(str(report.situation_by_user[u][sit]) for u in report.users)
        lines.append(f"| {sit} | {n} | {per_user} |")
    lines += ["", "## Users", "", "| User | Seen | Answered | Ignored | Cut short | Nudges |",
              "|---|---|---|---|---|---|"]
    for u in report.users.values():
        lines.append(f"| {u.name} | {u.seen} | {u.answered} | {u.ignored} | {u.cut_short} "
                     f"| {u.nudges} |")
    return "\n".join(lines) + "\n"


def write_report(report: SimReport, path: Path = DEFAULT_REPORT_PATH) -> Path:
    """Write the markdown report (creating folders) and return its path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_report(report))
    return path


def data_root() -> Path:
    """Folder holding match folders: $TIMING_SIM_DATA_ROOT or the repo's data/source."""
    return Path(os.environ.get(DATA_ROOT_ENV, REPO_ROOT / "data" / "source"))


def _load(match_id: str, root: Path) -> Match:
    from backend.ingest.loader import load_match  # local: loader may not exist in every branch
    return load_match(match_id, root=root)


@lru_cache(maxsize=8)
def _typical(match_id: str, root: str, seed: int) -> int:
    report = simulate(_load(match_id, Path(root)), seed=seed)
    return round(TYPICAL_ENGAGEMENT * report.users[NORMAL_USER].answered)


def typical_cards(match_id: str = DEFAULT_MATCH_ID, seed: int = DEFAULT_SEED) -> int:
    """Scoring n_typical: TYPICAL_ENGAGEMENT times the normal user's answered cards. Cached."""
    return _typical(match_id, str(data_root()), seed)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Replay a match through the timing agent.")
    ap.add_argument("--match-id", default=DEFAULT_MATCH_ID)
    ap.add_argument("--root", type=Path, default=None, help="folder of match folders")
    ap.add_argument("--out", type=Path, default=DEFAULT_REPORT_PATH)
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = ap.parse_args(argv)
    report = simulate(_load(args.match_id, args.root or data_root()), seed=args.seed)
    print(write_report(report, args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
