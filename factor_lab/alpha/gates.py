"""Alpha gate policy.

Only the deterministic gate thresholds that need calibration live here; the
policy file is repository source-of-truth (never under ``output/``).

Sample-size thresholds are deliberately **two independent levels**:

- ``min_dates_insufficient``: below this the evidence status is
  ``insufficient_evidence`` (never pass/fail);
- ``min_dates_warning``: below this (but above the insufficient floor) the
  verdict is still allowed, with a sample-size warning attached.

The two fields are edited independently; the only invariant is
``min_dates_warning >= min_dates_insufficient``.

Capacity is deliberately *not* a pass/fail gate: the research ADV capacity
estimate is reported as a metric and surfaced as a warning, but it cannot veto
a candidate.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from factor_lab.core import config
from factor_lab.core.store import read_json

GATES_SCHEMA = "factor_lab.alpha.gates.v2"


@dataclass(frozen=True)
class GatePolicy:
    schema: str = GATES_SCHEMA
    min_dates_insufficient: int = 30
    min_dates_warning: int = 60
    min_coverage: float = 0.5

    def __post_init__(self) -> None:
        if int(self.min_dates_insufficient) < 1:
            raise ValueError("min_dates_insufficient must be >= 1")
        if int(self.min_dates_warning) < int(self.min_dates_insufficient):
            raise ValueError("min_dates_warning must be >= min_dates_insufficient")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "min_dates_insufficient": int(self.min_dates_insufficient),
            "min_dates_warning": int(self.min_dates_warning),
            "min_coverage": float(self.min_coverage),
        }


def default_policy_path() -> Path:
    return config.ALPHA_POLICY_DIR / "gates.json"


def load_gate_policy(path: str | Path | None = None) -> GatePolicy:
    target = Path(path) if path is not None else default_policy_path()
    raw = read_json(target, default=None)
    if not isinstance(raw, dict):
        return GatePolicy()
    schema = raw.get("schema")
    if schema is not None and schema != GATES_SCHEMA:
        raise ValueError(f"unsupported gate policy schema: {schema}")
    return GatePolicy(
        min_dates_insufficient=int(raw.get("min_dates_insufficient", 30)),
        min_dates_warning=int(raw.get("min_dates_warning", 60)),
        min_coverage=float(raw.get("min_coverage", 0.5)),
    )
