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

import math
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
    from dataclasses import replace

    from factor_metrics import ALPHA_GATE_SPEC, CostModel

    from factor_lab.alpha.mandate import load_mandate

    policy = load_gate_policy()
    mandate = load_mandate()
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
        ),
        capacity=replace(ALPHA_GATE_SPEC.capacity, target_aum=float(mandate.aum_cny)),
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


LONG_ONLY_GATE_SCHEMA = "factor_lab.alpha.long_only_gate.v1"


def long_only_gate(
    metrics: dict[str, Any] | None,
    mandate: Any | None = None,
    *,
    capacity_metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Cost/impact-aware long-only promotion Gate.

    The Gate does not veto on a raw turnover number.  It requires positive
    after-fee net excess, t/IR, a cost budget relative to gross alpha and, when
    available, a capacity multiple above the policy floor.  Turnover remains a
    diagnostic warning because trading cost is already inside the net metric.
    """
    from factor_lab.alpha.mandate import load_mandate

    mandate = mandate or load_mandate()
    policy = dict(mandate.long_only_gate or {})
    metrics = metrics or {}
    reasons: list[str] = []
    warnings: list[str] = []
    try:
        n_dates = int(metrics.get("n_dates") or 0)
    except (TypeError, ValueError):
        n_dates = 0
    if n_dates < int(policy.get("min_dates_insufficient", 30)):
        reasons.append(f"long-only n_dates {n_dates} < {int(policy.get('min_dates_insufficient', 30))}")
    annualized = metrics.get("long_only_net_excess_annualized")
    t_stat = metrics.get("long_only_t")
    te = metrics.get("long_only_te_annualized")
    turnover = metrics.get("turnover")
    for name, value in (("annualized", annualized), ("t", t_stat), ("turnover", turnover)):
        try:
            if value is None or not math.isfinite(float(value)):
                reasons.append(f"long-only {name} missing/non-finite")
        except (TypeError, ValueError):
            reasons.append(f"long-only {name} missing/non-finite")

    horizon = int(metrics.get("horizon") or mandate.primary_horizon or 20)
    periods_per_year = 243.0 / max(1, horizon)
    gross_per_period = metrics.get("long_only_gross_excess_per_period")
    cost_per_period = metrics.get("cost_per_period")
    cost_ratio = None
    gross_annualized = None
    cost_annualized = None
    try:
        gross_annualized = float(gross_per_period) * periods_per_year
        cost_annualized = float(cost_per_period) * periods_per_year
        if gross_annualized > 0:
            cost_ratio = cost_annualized / gross_annualized
    except (TypeError, ValueError):
        pass
    if cost_ratio is None:
        warnings.append("gross/cost per-period metrics unavailable; cost-budget check skipped")
        if bool(policy.get("turnover_is_veto", False)) and turnover is not None:
            if float(turnover) > float(policy.get("max_turnover", 0.30)):
                reasons.append(f"long-only turnover above {float(policy.get('max_turnover', 0.30)):g} (legacy veto)")
    else:
        if cost_ratio > float(policy.get("max_cost_to_gross_ratio", 0.25)):
            reasons.append(
                f"annual trading cost is {cost_ratio:.1%} of gross excess; budget {float(policy.get('max_cost_to_gross_ratio', 0.25)):.0%}"
            )
        if bool(policy.get("turnover_is_veto", False)) and turnover is not None:
            if float(turnover) > float(policy.get("max_turnover", 0.30)):
                reasons.append(f"long-only turnover above {float(policy.get('max_turnover', 0.30)):g}")
        elif turnover is not None and float(turnover) > float(policy.get("turnover_warning", 0.50)):
            warnings.append(f"turnover {float(turnover):.2f} above warning {float(policy.get('turnover_warning', 0.50)):.2f}")
    coverage = metrics.get("coverage")
    if coverage is None:
        warnings.append("long-only coverage unavailable; coverage check skipped")
    else:
        try:
            if float(coverage) < float(policy.get("min_coverage", 0.90)):
                reasons.append(f"long-only coverage {float(coverage):.3f} below {float(policy.get('min_coverage', 0.90)):g}")
        except (TypeError, ValueError):
            reasons.append("long-only coverage missing/non-finite")
    capacity = metrics.get("capacity_multiple")
    if capacity is None and capacity_metrics is not None:
        capacity = capacity_metrics.get("capacity_multiple")
    capacity_multiple = None
    try:
        if capacity is not None and math.isfinite(float(capacity)):
            capacity_multiple = float(capacity)
    except (TypeError, ValueError):
        pass
    if capacity_multiple is None:
        warnings.append("capacity multiple unavailable; impact check skipped")
    elif capacity_multiple < float(policy.get("min_capacity_multiple", 2.0)):
        reasons.append(f"capacity multiple {capacity_multiple:g}x below {float(policy.get('min_capacity_multiple', 2.0)):g}x")
    if not reasons:
        if float(annualized) <= float(policy.get("min_net_excess_annualized", 0.0)):
            reasons.append("long-only net excess annualized is non-positive")
        if float(t_stat) < float(policy.get("min_t", 2.0)):
            reasons.append(f"long-only t below {float(policy.get('min_t', 2.0)):g}")
        try:
            ir = float(annualized) / float(te) if te and math.isfinite(float(te)) and float(te) > 0 else 0.0
        except (TypeError, ValueError):
            ir = 0.0
        if ir < float(policy.get("min_ir", 0.8)):
            reasons.append(f"long-only IR below {float(policy.get('min_ir', 0.8)):g}")
    status = "insufficient_evidence" if any("missing/non-finite" in reason for reason in reasons) else ("fail" if reasons else "pass")
    return {
        "schema": LONG_ONLY_GATE_SCHEMA,
        "verdict": status,
        "gate_version": str(policy.get("gate_version") or "cost_impact_v1"),
        "reasons": reasons,
        "warnings": warnings,
        "policy": {
            "min_net_excess_annualized": float(policy.get("min_net_excess_annualized", 0.0)),
            "min_t": float(policy.get("min_t", 2.0)),
            "min_ir": float(policy.get("min_ir", 0.8)),
            "max_cost_to_gross_ratio": float(policy.get("max_cost_to_gross_ratio", 0.25)),
            "min_capacity_multiple": float(policy.get("min_capacity_multiple", 2.0)),
            "min_coverage": float(policy.get("min_coverage", 0.90)),
            "turnover_warning": float(policy.get("turnover_warning", 0.50)),
            "turnover_is_veto": bool(policy.get("turnover_is_veto", False)),
            "min_dates_insufficient": int(policy.get("min_dates_insufficient", 30)),
            "primary_horizon": int(mandate.primary_horizon),
            "benchmark": mandate.benchmark_primary,
            "aum_cny": float(mandate.aum_cny),
        },
        "metrics": {
            "n_dates": n_dates,
            "long_only_net_excess_annualized": annualized,
            "long_only_t": t_stat,
            "long_only_te_annualized": te,
            "ir": (float(annualized) / float(te)) if te and math.isfinite(float(te)) and float(te) > 0 else None,
            "turnover": turnover,
            "gross_excess_annualized": gross_annualized,
            "cost_annualized": cost_annualized,
            "cost_to_gross_ratio": cost_ratio,
            "capacity_multiple": capacity_multiple,
            "coverage": coverage,
        },
    }
