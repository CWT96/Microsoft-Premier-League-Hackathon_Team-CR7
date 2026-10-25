"""timing.py — timing agent [2b]: interruption cost, gate, director policy and moments.

Last Modified: 2026-10-24 22:55, first version from SPEC section 4
Inputs:  one DetectorState per match second (in (half, t) order)
Outputs: TimingOutput (Moments and TimingDecisions) per step; no files

Workflow:
  - interruption_cost() turns raw detector signals into a 0-1 cost with situational overrides
  - TimingAgent.step() applies hysteresis, the gate (half end, after attack, spacing) and
    asks the policy (RulePolicy, or LLMPolicy with rule fallback) whether to open or hold
  - scheduled moments (kickoff and half-time stacks, water break, player slots) bypass the policy
  - timing decides WHEN and WHY only; question content belongs to the generator

CAUTION: the LLM callable is injected (signature of agents.llm.complete_json); this module
never imports it. Any failure of that callable falls back to RulePolicy.
"""

from __future__ import annotations

import copy
import json
import logging
import math
import re
import zlib
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable, Iterable, get_args

from backend.agents import timing_rules as R
from backend.core.constants import HALF_LENGTH_S, NOMINAL_HALF_END_S
from backend.core.types import (
    BallZone, BreakKind, Category, DetectorState, LockRule, Mode, Moment, MomentKind,
    MomentSituation, Situation, TimingAction,
    TimingDecision, TimingOutput, TimingSource,
)

logger = logging.getLogger(__name__)
_WARNED: set[str] = set()  # module-level warn-once keys
_KNOWN_VALUES = {
    "ball_zone": set(get_args(BallZone)), "situation": set(get_args(Situation)),
    "mode": set(get_args(Mode)), "break_kind": set(get_args(BreakKind)),
}

LLMComplete = Callable[..., "dict[str, Any] | None"]

POLICY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["action", "reason"],
    "additionalProperties": False,
    "properties": {
        "action": {"type": "string", "enum": ["open", "hold"]},
        "tone": {"type": ["string", "null"], "maxLength": R.TONE_MAX_CHARS},
        "reason": {"type": "string", "maxLength": R.REASON_MAX_CHARS},
    },
}

_NUMBER = re.compile(r"\d+(?:\.\d+)?")
_ZONE_PHRASE = {"own_third": "defence", "middle_third": "midfield", "final_third": "attack"}


# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
#
# Interruption cost
#
# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%


def _warn_once(key: str, msg: str, *args: Any) -> None:
    if key not in _WARNED:
        _WARNED.add(key)
        logger.warning(msg, *args)


def _ramp(x: float, zero_at: float, one_at: float) -> float:
    """Linear score: 0 at ``zero_at``, 1 at ``one_at``, clipped (works in either direction)."""
    return min(1.0, max(0.0, (x - zero_at) / (one_at - zero_at)))


def _context_score(state: DetectorState) -> float:
    """Tense end of a half with a close score is high; big lead or early half is low."""
    gap = abs(state.score_home - state.score_away)
    half_end = NOMINAL_HALF_END_S[state.half]
    elapsed = state.t - (half_end - HALF_LENGTH_S)
    if gap >= R.CONTEXT_BIG_LEAD_GOALS or elapsed < R.CONTEXT_EARLY_S:
        return R.CONTEXT_RELAXED
    if gap <= R.CONTEXT_CLOSE_GOALS and half_end - state.t <= R.CONTEXT_TENSE_WINDOW_S:
        return R.CONTEXT_TENSE
    return R.CONTEXT_NEUTRAL


def _near_box(state: DetectorState) -> bool:
    """Free kick close enough to goal to be a set piece."""
    if state.ball_xy is not None:
        return state.ball_xy[0] >= R.NEAR_BOX_MIN_X
    return state.ball_zone == "final_third"


def moment_situation(state: DetectorState) -> MomentSituation | None:
    """Map the detector's situation to the moment vocabulary; None = no moment possible."""
    sit = state.situation
    if state.attack_active or state.mode == "attack" or sit == "attack":
        return None
    if sit in ("open_play", "kickoff"):
        return "calm_play"
    if sit in ("corner", "penalty") or (sit == "free_kick" and _near_box(state)):
        return "set_piece_wait" if state.restart_pending else None
    if sit == "free_kick":  # far from goal: a foul was given
        return "referee_decision" if state.restart_pending else "calm_play"
    if sit in ("stalled_near_box", "throw_in", "goal_kick", "referee_decision", "substitution",
               "injury", "after_goal"):
        return sit  # type: ignore[return-value]
    return None  # water_break (scheduled instead), half_time, full_time


def _since_goal(state: DetectorState, goal_t: float | None) -> float | None:
    """Seconds since the goal: the agent's own memory first, the detector's attack end second."""
    ref = goal_t if goal_t is not None else state.last_attack_end_t
    return None if ref is None else state.t - ref


def _override(state: DetectorState, goal_t: float | None = None) -> tuple[float, str] | None:
    """Situational override (cost, name) that replaces the formula, or None."""
    sit = state.situation
    if state.attack_active or state.mode == "attack" or sit == "attack":
        return R.COST_SILENT, "fast_attack"
    if sit == "after_goal" or any(e.type == "goal" for e in state.new_events):
        since = _since_goal(state, goal_t)
        if since is None or since < R.GOAL_SILENT_S:  # unknown goal time: stay silent
            return R.COST_SILENT, "goal_celebration"
        return R.COST_AFTER_GOAL, "after_goal"
    ms = moment_situation(state)
    if ms == "set_piece_wait":
        return R.COST_SET_PIECE_WAIT, "set_piece_wait"
    if sit in ("corner", "penalty", "free_kick") and ms is None:
        return R.COST_LIVE_SET_PIECE, "set_piece_live"
    return {
        "referee_decision": (R.COST_REFEREE, "referee_decision"),
        "stalled_near_box": (R.COST_STALLED_NEAR_BOX, "stalled_near_box"),
        "throw_in": (R.COST_STOPPAGE_RESTART, "restart"),
        "goal_kick": (R.COST_STOPPAGE_RESTART, "restart"),
        "substitution": (R.COST_BREAK_IN_PLAY, "stoppage"),
        "injury": (R.COST_BREAK_IN_PLAY, "stoppage"),
    }.get(ms or "")


def interruption_cost(state: DetectorState,
                      goal_t: float | None = None) -> tuple[float, dict[str, Any]]:
    """Cost of looking away for the next ~6 s: 0 = nothing to miss, 1 = do not look away.

    Args:
        state: the detector's raw signals for one match second.
        goal_t: when the agent saw the latest goal (None = unknown, falls back to the detector).

    Returns:
        (cost in [0, 1], sub-scores). Sub-scores: ``zone``, ``threat``, ``movement``,
        ``pressure``, ``shot_gap``, ``context`` (each 0-1, ``None`` when unavailable) and
        ``override`` (name of the situational override or ``None``).
    """
    movement = state.movement
    if movement is not None and math.isnan(movement):
        _warn_once("nan_movement", "NaN movement treated as missing tracking")
        movement = None
    sig: dict[str, Any] = {
        "zone": R.ZONE_SCORES.get(state.ball_zone) if state.ball_zone else None,
        "threat": _ramp(state.threat, *R.THREAT_RAMP),
        "movement": None if movement is None else min(1.0, max(0.0, movement)),
        "pressure": None if state.pressure_m is None else _ramp(state.pressure_m,
                                                                *R.PRESSURE_RAMP_M),
        "shot_gap": _ramp(state.seconds_since_shot, *R.SHOT_GAP_RAMP_S),
        "context": _context_score(state),
    }
    avail = {k: w for k, w in R.SIGNAL_WEIGHTS.items() if sig[k] is not None}
    formula = sum(sig[k] * w for k, w in avail.items()) / sum(avail.values())
    over = _override(state, goal_t)
    sig["override"] = over[1] if over else None
    return (over[0] if over else formula), sig


# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
#
# Director policies
#
# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%


@dataclass(frozen=True)
class PolicyResult:
    action: str  # "open" | "hold"
    tone: str | None
    reason: str
    source: TimingSource
    fallback_reason: str | None = None


def describe(payload: dict[str, Any]) -> str:
    """Plain-English reason built from the policy input (no numbers)."""
    ms = payload["moment_situation"]
    zone = _ZONE_PHRASE.get(payload.get("ball_zone") or "", "play")
    movement = payload["signals"].get("movement")
    walking = movement is not None and movement < R.WALKING_MOVEMENT
    restart = (payload.get("situation_detail") or {}).get("restart", "set piece").replace("_", " ")
    text = {
        "calm_play": f"Calm play in {zone}, low threat. Nothing to miss.",
        "stalled_near_box": "Attack has stalled near the box, no shot for a while.",
        "throw_in": f"Throw-in in {zone}, " + ("players walking back." if walking
                                               else "play stopped."),
        "goal_kick": "Goal kick, both teams resetting.",
        "set_piece_wait": f"{restart.capitalize()} awarded, everyone waiting for the kick.",
        "referee_decision": "Referee decision, play stopped and the room is arguing.",
        "substitution": "Substitution, nothing to miss for a while.",
        "injury": "Injury stoppage, play is paused.",
        "after_goal": "Goal celebrations are over, play restarting.",
    }.get(ms, "The match can spare a glance.")
    return text[: R.REASON_MAX_CHARS]


def _tone_for(situation: str, moment_id: str) -> str:
    tones = R.TONES[situation]
    return tones[zlib.crc32(moment_id.encode()) % len(tones)]


class RulePolicy:
    """Deterministic director: always open; tone from a fixed list per situation."""

    def decide(self, payload: dict[str, Any], moment_id: str, match_id: str) -> PolicyResult:
        return PolicyResult("open", _tone_for(payload["moment_situation"], moment_id),
                            describe(payload), "rule")


class LLMPolicy:
    """Director backed by an injected ``complete_json``-style callable, with rule fallback."""

    def __init__(self, llm_complete: LLMComplete, fallback: RulePolicy | None = None) -> None:
        self._complete = llm_complete
        self._fallback = fallback or RulePolicy()

    def decide(self, payload: dict[str, Any], moment_id: str, match_id: str) -> PolicyResult:
        prompt = json.dumps(payload, sort_keys=True)
        try:
            raw = self._complete(R.POLICY_AGENT, prompt, match_id, moment_id, POLICY_SCHEMA,
                                 R.POLICY_TIMEOUT_S)
            why = "no answer" if raw is None else None
        except Exception as exc:  # noqa: BLE001 - injected callable: any failure falls back
            logger.exception("timing LLM call failed for %s", moment_id)
            raw, why = None, f"llm call raised {type(exc).__name__}"
        if raw is not None:
            result, why = self._validate(raw, prompt)
            if result is not None:
                return result
        logger.warning("timing LLM answer for %s rejected (%s); using rules", moment_id, why)
        fb = self._fallback.decide(payload, moment_id, match_id)
        return PolicyResult(fb.action, fb.tone, fb.reason, "rule_fallback", why)

    @staticmethod
    def _validate(raw: Any, prompt: str) -> tuple[PolicyResult | None, str | None]:
        """(result, None) if the answer is usable, else (None, why it was rejected)."""
        if not isinstance(raw, dict):
            return None, "answer is not an object"
        if raw.get("action") not in ("open", "hold"):
            return None, "action is not open or hold"
        tone, reason = raw.get("tone"), raw.get("reason")
        if not isinstance(reason, str) or not reason or len(reason) > R.REASON_MAX_CHARS:
            return None, "reason missing or too long"
        if tone is not None and (not isinstance(tone, str) or len(tone) > R.TONE_MAX_CHARS):
            return None, "tone invalid"
        if tone is not None and any(c.isdigit() for c in tone):
            return None, "tone contains digits"
        prompt_numbers = set(_NUMBER.findall(prompt))
        if any(n not in prompt_numbers for n in _NUMBER.findall(reason)):  # number guard
            return None, "reason has numbers not in the input"
        return PolicyResult(raw["action"], tone, reason, "llm"), None


# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
#
# Timing agent
#
# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%


@dataclass(frozen=True)
class NextStop:
    """Where ``skip: next`` should land: a calm-spell start or a scheduled moment."""

    half: int
    t: float
    reason: str  # "calm_spell" | "scheduled"
    moment: Moment | None


class TimingAgent:
    """Reads one DetectorState per match second and emits Moments and TimingDecisions.

    Call ``step`` once per second in (half, t) order. Deterministic for a given stream.
    A live step whose (half, t) is not after the previous live step is logged and ignored.
    The kickoff stack is emitted in mode ``lobby``, or on a live step at half 1 and t <= 0
    when no lobby was seen; a stream that starts later (resume or seek) gets no stack.
    """

    def __init__(self, policy: RulePolicy | LLMPolicy, match_id: str, roster: Any = None) -> None:
        self._policy = policy
        self._match_id = match_id
        self._roster = roster
        self._next_id = 1
        self._kickoff_done = False
        self._prev_break_kind: str | None = None
        self._silent = False
        self._calm_since: float | None = None
        self._last_open: tuple[int, float] | None = None
        self._blocked_rule: str | None = None
        self._holds = 0
        self._hold_until: tuple[int, float] | None = None
        self._set_piece_open = False
        self._players_done: set[int] = set()
        self._recent: deque[str] = deque(maxlen=R.RECENT_SITUATIONS)
        self._goal_t: float | None = None  # agent's own memory of the latest goal
        self._goal_half = 0
        self._last_live_key: tuple[int, float] | None = None
        self._warned_values: set[tuple[str, str]] = set()

    # .........................................................................
    # public
    # .........................................................................

    def step(self, state: DetectorState) -> TimingOutput:
        """Process one match second."""
        moments: list[Moment] = []
        decisions: list[TimingDecision] = []
        self._check_values(state)
        live = state.mode in ("live", "attack")
        key = (state.half, state.t)
        if live and self._last_live_key is not None and key <= self._last_live_key:
            logger.warning("timing step %s is not after %s; ignored", key, self._last_live_key)
            return TimingOutput()
        if live:
            self._last_live_key = key
        self._note_goal(state)
        cost, sig = interruption_cost(state, self._goal_t)
        if not self._recent or self._recent[-1] != state.situation:
            self._recent.append(state.situation)

        if state.mode == "lobby":
            self._kickoff(state, cost, sig, moments, decisions)
        elif state.mode == "full_time":
            self._reset_flow()
        elif state.mode == "break":
            self._on_break(state, cost, sig, moments, decisions)
        else:
            if not self._kickoff_done and state.half == 1 and state.t <= 0:
                self._kickoff(state, cost, sig, moments, decisions)
            self._kickoff_done = True
            self._prev_break_kind = None
            self._live(state, cost, sig, moments, decisions)
        return TimingOutput(tuple(moments), tuple(decisions))

    def _check_values(self, state: DetectorState) -> None:
        """Log once per unknown categorical value (detector and agent out of step)."""
        for name in ("ball_zone", "situation", "mode", "break_kind"):
            value = getattr(state, name)
            if value is not None and value not in _KNOWN_VALUES[name] \
                    and (name, value) not in self._warned_values:
                self._warned_values.add((name, value))
                logger.warning("unknown %s %r from the detector", name, value)

    def _note_goal(self, state: DetectorState) -> None:
        """Remember when the latest goal happened, independent of the detector's clocks."""
        if any(e.type == "goal" for e in state.new_events):
            self._goal_t, self._goal_half = state.t, state.half
        elif state.situation == "after_goal" and self._goal_t is None:
            self._goal_t, self._goal_half = state.t, state.half
        elif state.situation != "after_goal" and self._goal_t is not None and (
                state.half != self._goal_half or state.t - self._goal_t >= R.GOAL_SILENT_S):
            self._goal_t = None

    def peek_next_stop(self, states: Iterable[DetectorState]) -> NextStop | None:
        """First calm-spell start or scheduled moment in ``states``; does not change ``self``."""
        sim = copy.deepcopy(self, {id(self._policy): RulePolicy()})  # no LLM calls on a skip
        for s in states:
            out = sim.step(s)
            for m in out.moments:
                if m.kind != "instant":
                    return NextStop(s.half, s.t, "scheduled", m)
            if any(d.action == "calm" for d in out.decisions):
                return NextStop(s.half, s.t, "calm_spell", None)
        return None

    # .........................................................................
    # scheduled moments
    # .........................................................................

    def _kickoff(self, state, cost, sig, moments, decisions) -> None:
        if self._kickoff_done:
            return
        self._kickoff_done = True
        self._stack(state, sig, "kickoff_stack", "kickoff", "Kickoff stack: two match forecasts "
                    "and one favourite-player forecast.", moments, decisions, half=1)

    def _on_break(self, state, cost, sig, moments, decisions) -> None:
        entering = state.break_kind != self._prev_break_kind
        self._prev_break_kind = state.break_kind
        self._reset_flow()
        if not entering:
            return
        if state.break_kind == "half_time":
            self._last_open = None
            self._stack(state, sig, "ht_stack", "half_time", "Half time: two second-half "
                        "forecasts and one favourite-player forecast.", moments, decisions,
                        half=2)
        elif state.break_kind == "water_break":
            m = self._moment(state, "water_break", "forecast", "water_break", 0.0,
                             R.WATER_BREAK_WINDOW_S)
            moments.append(m)
            self._last_open = (state.half, state.t)
            decisions.append(self._decision(
                state, "scheduled", "schedule", "Drinks break, one card for everyone.", sig, 0.0,
                (m.moment_id,), "water_break"))

    def _stack(self, state, sig, kind: MomentKind, situation: MomentSituation, reason: str,
               moments: list, decisions: list, half: int) -> None:
        stack = [self._moment(state, kind, cat, situation, 0.0, R.STACK_WINDOW_S, half=half)
                 
for cat in R.STACK_CATEGORIES]
        moments.extend(stack)
        self._last_open = (state.half, state.t)
        decisions.append(self._decision(state, "scheduled", "schedule", reason, sig, 0.0,
                                        tuple(m.moment_id for m in stack), situation))

    # .........................................................................
    # live play
    # .........................................................................

    def _live(self, state, cost, sig, moments, decisions) -> None:
        was_silent = self._silent
        self._silent = cost >= R.SILENT_COST or (was_silent and cost > R.CANDIDATE_COST)
        calm = cost <= R.CANDIDATE_COST and not self._silent
        if self._silent and not was_silent:
            decisions.append(self._decision(state, "silent", "rule", self._silent_reason(sig),
                                            sig, cost, (), state.situation))
        if calm and self._calm_since is None:
            self._calm_since = state.t
            decisions.append(self._decision(state, "calm", "rule", "Play is calm, the match can "
                                            "spare a glance.", sig, cost, (), state.situation))
        elif not calm:
            self._calm_since = None
        ms = moment_situation(state)
        if ms != "set_piece_wait":
            self._set_piece_open = False
        if not calm or ms is None or (ms == "set_piece_wait" and self._set_piece_open):
            self._blocked_rule = None if not calm or ms is None else self._blocked_rule
            self._holds = 0
            self._hold_until = None
            return
        if self._hold_until is not None and self._hold_until[0] == state.half \
                and state.t < self._hold_until[1]:
            return
        rule = self._gate(state)
        if rule:
            if rule not in R.QUIET_BLOCK_RULES and rule != self._blocked_rule:
                decisions.append(self._decision(
                    state, "blocked", "rule", self._block_reason(rule), sig, cost, (),
                    state.situation, blocked_by=rule))
                self._blocked_rule = rule
            return
        self._blocked_rule = None
        self._open(state, cost, sig, ms, moments, decisions)

    def _open(self, state, cost, sig, ms, moments, decisions) -> None:
        slot = self._due_player_slot(state)
        if slot is not None:
            self._players_done.add(slot)
            m = self._moment(state, "player_instant", "player", ms, cost, R.CARD_WINDOW_S,
                             detail=state.situation_detail)
            self._record_open(state, ms, m, moments)
            decisions.append(self._decision(
                state, "scheduled", "schedule", "Player slot reached at the first calm second.",
                sig, cost, (m.moment_id,), ms))
            return
        moment_id = f"q-{self._next_id}"
        payload = self._payload(state, cost, sig, ms)
        res = self._policy.decide(payload, moment_id, self._match_id)
        extra: dict[str, Any] = {}
        if res.fallback_reason:
            extra["fallback_reason"] = res.fallback_reason
        if res.action == "hold" and self._holds < R.MAX_HOLDS_IN_ROW:
            self._holds += 1
            self._hold_until = (state.half, state.t + R.HOLD_DELAY_S)
            decisions.append(self._decision(state, "hold", res.source, res.reason, sig, cost, (),
                                            ms, tone=res.tone, extra=extra))
            return
        if res.action == "hold":  # holds used up: open with the rule tone and reason
            rule = RulePolicy().decide(payload, moment_id, self._match_id)
            reason = f"{rule.reason} (after {R.MAX_HOLDS_IN_ROW} holds)"
            res = PolicyResult("open", rule.tone, reason[: R.REASON_MAX_CHARS], "rule",
                               res.fallback_reason)
            extra["holds_exhausted"] = True
        m = self._moment(state, "instant", "instant", ms, cost, R.CARD_WINDOW_S, tone=res.tone,
                         detail=state.situation_detail)
        self._record_open(state, ms, m, moments)
        decisions.append(self._decision(state, "open", res.source, res.reason, sig, cost,
                                        (m.moment_id,), ms, tone=res.tone, extra=extra))

    def _record_open(self, state, ms, m: Moment, moments: list) -> None:
        moments.append(m)
        self._last_open = (state.half, state.t)
        self._holds = 0
        self._hold_until = None
        if ms == "set_piece_wait":
            self._set_piece_open = True

    def _due_player_slot(self, state: DetectorState) -> int | None:
        for half, at in R.PLAYER_SLOTS:
            if state.half == half and state.t >= at and half not in self._players_done:
                return half
        return None

    def _gate(self, state: DetectorState) -> str | None:
        """Name of the first hard rule blocking a candidate, or None."""
        if state.t >= NOMINAL_HALF_END_S[state.half] - R.HALF_END_GUARD_S:
            return "half_end"
        ends = [state.t - e for e in (self._goal_t, state.last_attack_end_t) if e is not None]
        if any(0 <= since < R.AFTER_ATTACK_S for since in ends):  # negative = stale, ignored
            return "after_attack"
        if (self._last_open is not None and self._last_open[0] == state.half
                and state.t - self._last_open[1] < R.MIN_SPACING_S):
            return "spacing"
        return None

    # .........................................................................
    # builders
    # .........................................................................

    def _moment(self, state, kind: MomentKind, category: Category, situation: MomentSituation,
                cost: float, window_s: float, tone: str | None = None,
                detail: dict[str, Any] | None = None, half: int | None = None) -> Moment:
        lock_rule: LockRule = "on_restart" if situation == "set_piece_wait" else "fixed"
        m = Moment(
            moment_id=f"q-{self._next_id}", kind=kind, category=category, open_at=state.t,
            lock_at=state.t + window_s, lock_rule=lock_rule, situation=situation,
            cost=round(cost, 3), half=half or state.half, tone=tone,
            situation_detail=dict(detail or {}))
        self._next_id += 1
        return m

    def _payload(self, state, cost, sig, ms) -> dict[str, Any]:
        calm_for = 0.0 if self._calm_since is None else state.t - self._calm_since
        return {
            "t": state.t, "half": state.half,
            "score": {"home": state.score_home, "away": state.score_away},
            "cost": round(cost, 3),
            "signals": {k: (None if sig[k] is None else round(sig[k], 3))
                        for k in ("zone", "threat", "movement", "pressure", "shot_gap",
                                  "context")},
            "situation": state.situation, "moment_situation": ms,
            "situation_detail": dict(state.situation_detail),
            "ball_zone": state.ball_zone, "possession_team": state.possession_team,
            "calm_for_s": calm_for, "last_situations": list(self._recent),
        }

    def _decision(self, state, action: TimingAction, source: TimingSource, reason: str, sig,
                  cost: float, moment_ids: tuple[str, ...], situation: str | None,
                  tone: str | None = None, blocked_by: str | None = None,
                  extra: dict[str, Any] | None = None) -> TimingDecision:
        calm_for = 0.0 if self._calm_since is None else state.t - self._calm_since
        inputs = {
            "cost": round(cost, 3), "ball_zone": state.ball_zone,
            "threat": _r(sig["threat"]), "movement": _r(sig["movement"]),
            "pressure": _r(sig["pressure"]), "shot_gap": _r(sig["shot_gap"]),
            "context": _r(sig["context"]), "calm_for_s": calm_for,
        }
        inputs.update(extra or {})
        return TimingDecision(
            t=state.t, half=state.half, action=action, source=source,
            reason=reason[: R.REASON_MAX_CHARS], moment_ids=moment_ids, situation=situation,
            tone=tone, blocked_by=blocked_by, inputs=inputs)

    def _reset_flow(self) -> None:
        self._silent = False
        self._calm_since = None
        self._blocked_rule = None
        self._holds = 0
        self._hold_until = None
        self._set_piece_open = False

    @staticmethod
    def _silent_reason(sig: dict[str, Any]) -> str:
        return {
            "fast_attack": "Fast attack in motion, nothing is asked.",
            "goal_celebration": "Goal just scored, letting the room celebrate.",
            "set_piece_live": "Set piece is live in the box, nothing is asked.",
        }.get(sig["override"], "A chance is building, nothing is asked.")

    @staticmethod
    def _block_reason(rule: str) -> str:
        return {
            "half_end": "Half is about to end, too late for an answer window.",
            "after_attack": "Attack just ended, the catch-up card shows first.",
        }.get(rule, f"Blocked by {rule}.")


def _r(x: float | None) -> float | None:
    return None if x is None else round(x, 3)
