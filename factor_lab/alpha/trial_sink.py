"""Append-only unified trial event sink.

The legacy memory attempts and ``trials.jsonl`` remain the evidence stores used
by the current FDR. This sink is the single place where every label-touching
evaluation event should also be recorded, including screens, dev cycles,
holdout confirmations and ML experiments.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from factor_lab.core import config


SINK_SCHEMA = "factor_lab.alpha.trial_event.v1"
STAGES = {"screen", "dev", "ml", "holdout", "team"}


def trial_sink_path() -> Path:
    import os

    override = os.environ.get("FACTOR_LAB_TRIALS_DIR")
    path = (Path(override) if override else Path(config.LAB_ROOT) / "alpha") / "trial_events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def record_event(stage: str, payload: dict[str, Any]) -> dict[str, Any]:
    if stage not in STAGES:
        raise ValueError(f"invalid trial stage: {stage}")
    row = {
        "schema": SINK_SCHEMA,
        "stage": stage,
        **dict(payload or {}),
        "recorded_at": config.now_iso(),
    }
    with trial_sink_path().open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    return row


def read_events() -> list[dict[str, Any]]:
    path = trial_sink_path()
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def event_summary() -> dict[str, Any]:
    rows = read_events()
    by_stage: dict[str, int] = {}
    for row in rows:
        by_stage[str(row.get("stage"))] = by_stage.get(str(row.get("stage")), 0) + 1
    return {"schema": "factor_lab.alpha.trial_event_summary.v1", "n": len(rows), "by_stage": by_stage, "path": str(trial_sink_path())}
