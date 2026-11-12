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

Transaction costs are the actual A-share broker/tax schedule (commission,
handling, regulatory, transfer on both sides; stamp duty on the sell side) plus
annual short borrow. The generic 20bps research CostModel is not used by
Alpha campaigns.

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
    min_coverage: float = 0.90
    min_net_t: float = 2.0
    borrow_bps_per_year: float = 0.0
    commission_bps: float = 1.8
    handling_bps: float = 0.34
    regulatory_bps: float = 0.20
    transfer_bps: float = 0.10
    stamp_duty_bps: float = 5.0

    def __post_init__(self) -> None:
        if int(self.min_dates_insufficient) < 1:
            raise ValueError("min_dates_insufficient must be >= 1")
        if int(self.min_dates_warning) < int(self.min_dates_insufficient):
            raise ValueError("min_dates_warning must be >= min_dates_insufficient")
        if float(self.min_net_t) < 0:
            raise ValueError("min_net_t must be >= 0")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "min_dates_insufficient": int(self.min_dates_insufficient),
            "min_dates_warning": int(self.min_dates_warning),
            "min_coverage": float(self.min_coverage),
            "min_net_t": float(self.min_net_t),
            "borrow_bps_per_year": float(self.borrow_bps_per_year),
            "commission_bps": float(self.commission_bps),
            "handling_bps": float(self.handling_bps),
            "regulatory_bps": float(self.regulatory_bps),
            "transfer_bps": float(self.transfer_bps),
            "stamp_duty_bps": float(self.stamp_duty_bps),
        }


def alpha_gate_spec():
    from factor_metrics import ALPHA_GATE_SPEC, CostModel

    policy = load_gate_policy()
    buy_fee = float(policy.commission_bps) + float(policy.handling_bps) + float(policy.regulatory_bps) + float(policy.transfer_bps)
    sell_fee = buy_fee + float(policy.stamp_duty_bps)
    return ALPHA_GATE_SPEC.replace(
        cost=CostModel(
            bps=20.0,
            convention="two_leg",
            bps_meaning="per_side",
            borrow_bps_per_year=float(policy.borrow_bps_per_year),
            buy_fee_bps=buy_fee,
            sell_fee_bps=sell_fee,
        )
    )


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
        min_coverage=float(raw.get("min_coverage", 0.90)),
        min_net_t=float(raw.get("min_net_t", 2.0)),
        borrow_bps_per_year=float(raw.get("borrow_bps_per_year", 0.0)),
        commission_bps=float(raw.get("commission_bps", 1.8)),
        handling_bps=float(raw.get("handling_bps", 0.34)),
        regulatory_bps=float(raw.get("regulatory_bps", 0.20)),
        transfer_bps=float(raw.get("transfer_bps", 0.10)),
        stamp_duty_bps=float(raw.get("stamp_duty_bps", 5.0)),
    )
