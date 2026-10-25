"""Detector [1] against the hand-labelled stretches; writes data/generated/detector_eval.md.

Set DETECTOR_EVAL_DATA to the match folder (default data/source/sim-placeholder). The test is
skipped when the folder or the ingest loader is missing. Misses are reported, never failed,
except two sanity asserts: every attack label overlaps an attack-mode second, and no calm label
is mostly attack-mode.
"""

from __future__ import annotations

import json
import os
from collections import defaultdict
from pathlib import Path

import pytest

from backend.core.constants import RESTART_TYPES
from backend.core.types import DetectorState
from backend.detect.detector import Detector

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA = REPO_ROOT / "data" / "source" / "sim-placeholder"
DATA_DIR = Path(os.environ.get("DETECTOR_EVAL_DATA", DEFAULT_DATA))
REPORT_PATH = REPO_ROOT / "data" / "generated" / "detector_eval.md"
CALM_MAX_ATTACK_SHARE = 0.5
WATER_MIN_SHARE = 0.8
STOPPAGE_SITUATIONS = RESTART_TYPES | {"referee_decision", "substitution", "injury", "after_goal"}


def _load(with_tracking: bool):
    try:
        from backend.ingest.loader import load_match
    except ImportError:
        pytest.skip("ingest loader not available in this branch")
    roster = json.loads((DATA_DIR / "roster.json").read_text())
    return load_match(roster["match_id"], root=DATA_DIR.parent, with_tracking=with_tracking)


def _states(with_tracking: bool) -> list[DetectorState]:
    match = _load(with_tracking)
    return list(Detector(match.roster, match.roster.home, match.roster.away).run(match))


def _in_label(states: list[DetectorState], label: dict) -> list[DetectorState]:
    return [s for s in states if s.half == label["half"] and s.mode != "lobby"
            and label["t_start"] - 1 < s.t <= label["t_end"]]


def _judge(kind: str, span: list[DetectorState]) -> tuple[bool, str]:
    if not span:
        return False, "no states in span"
    n = len(span)
    attack = sum(s.mode == "attack" for s in span) / n
    dull = sum(s.dull_score for s in span) / n
    if kind == "attack":
        return attack > 0, f"attack-mode share {attack:.2f}"
    if kind == "calm":
        return attack <= CALM_MAX_ATTACK_SHARE, f"attack share {attack:.2f}, mean dull {dull:.2f}"
    if kind == "set_piece_wait":
        k = sum(s.restart_pending for s in span)
        return k > 0, f"restart-pending seconds {k}/{n}"
    if kind == "stoppage":
        k = sum(s.situation in STOPPAGE_SITUATIONS for s in span)
        return k > 0, f"stoppage-situation seconds {k}/{n}"
    if kind == "water_break":
        k = sum(s.break_kind == "water_break" for s in span)
        return k / n >= WATER_MIN_SHARE, f"water-break seconds {k}/{n}"
    k = sum(s.situation == "stalled_near_box" for s in span)
    return k > 0, f"stalled seconds {k}/{n}"


def _evaluate(states: list[DetectorState], labels: list[dict]):
    rows = []
    for lab in labels:
        ok, note = _judge(lab["kind"], _in_label(states, lab))
        rows.append((lab, ok, note))
    return rows


def _table(title: str, rows) -> list[str]:
    by_kind: dict[str, list[bool]] = defaultdict(list)
    for lab, ok, _ in rows:
        by_kind[lab["kind"]].append(ok)
    out = [f"## {title}", "", "| kind | hits | misses |", "|---|---|---|"]
    for kind, oks in sorted(by_kind.items()):
        out.append(f"| {kind} | {sum(oks)} | {len(oks) - sum(oks)} |")
    out += ["", "Misses:", ""]
    misses = [(lab, note) for lab, ok, note in rows if not ok]
    out += [f"- {lab['kind']} half {lab['half']} {lab['t_start']}-{lab['t_end']}: {note} "
            f"({lab.get('note', '')})" for lab, note in misses] or ["- none"]
    return out + [""]


def test_detector_against_labels() -> None:
    if not (DATA_DIR / "labels.json").exists():
        pytest.skip(f"no evaluation data at {DATA_DIR}")
    labels = json.loads((DATA_DIR / "labels.json").read_text())
    runs = {"with tracking": _states(True)}
    if (DATA_DIR / "tracking.csv").exists():
        runs["without tracking"] = _states(False)
    lines = ["# Detector evaluation", "", f"Data: `{DATA_DIR.name}`, {len(labels)} labels. "
             "A hit means the detector reported the labelled situation inside the span.", ""]
    all_rows = {}
    for title, states in runs.items():
        all_rows[title] = _evaluate(states, labels)
        lines += _table(title, all_rows[title])
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text("\n".join(lines))

    for title, rows in all_rows.items():
        for lab, ok, note in rows:
            if lab["kind"] == "attack":
                assert ok, f"{title}: attack label {lab['t_start']} never attack-mode"
            if lab["kind"] == "calm":
                assert ok, f"{title}: calm label {lab['t_start']} mostly attack-mode ({note})"
