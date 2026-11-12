"""Deterministic research runtime and evaluator for Alpha artifacts."""

from __future__ import annotations

import math
from typing import Any

from factor_lab.alpha.gates import GatePolicy, load_gate_policy
from factor_lab.alpha.data import AlphaDataset
from factor_lab.alpha.evaluation import evaluate_expression
from factor_lab.alpha.memory import make_edge_card


RESULT_SCHEMA = "factor_lab.alpha.research_result.v1"

#: Metrics that must be finite before a verdict can be pass/fail.  Missing or
#: non-finite values mean "insufficient evidence", never "pass" or "fail".
REQUIRED_METRICS = ("net_spread", "ic_mean", "turnover")


def run_research(
    expression: str,
    dataset: AlphaDataset,
    *,
    factor_ref: dict[str, Any] | None = None,
    horizon: int = 5,
    cost_bps: float = 20.0,
    universe: Any | None = None,
) -> dict[str, Any]:
    metrics = evaluate_expression(
        expression,
        dataset.context,
        horizon=horizon,
        cost_bps=cost_bps,
        start=dataset.start,
        end=dataset.end,
        universe=universe,
    )
    return {
        "schema": RESULT_SCHEMA,
        "factor_ref": factor_ref or {},
        "expression": expression,
        "metrics": metrics,
        "data_manifest": dataset.data_manifest,
        "horizon": horizon,
        "cost_bps": cost_bps,
    }


def _finite(value: Any) -> bool:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(number)


def evidence_status(
    metrics: dict[str, Any], policy: GatePolicy | None = None
) -> str:
    """Tri-state evidence status: pass / fail / insufficient_evidence.

    ``None``/``NaN`` metrics or too few observations must never be classified
    as pass or fail; they are ``insufficient_evidence``.
    """
    policy = policy or load_gate_policy()
    missing = [name for name in REQUIRED_METRICS if not _finite(metrics.get(name))]
    try:
        n_dates = int(metrics.get("n_dates") or 0)
    except (TypeError, ValueError):
        n_dates = 0
    if missing or n_dates < int(policy.min_dates_insufficient):
        return "insufficient_evidence"
    reasons = _failure_reasons(metrics)
    return "fail" if reasons else "pass"


def _failure_reasons(metrics: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    net = metrics.get("net_spread")
    ic = metrics.get("ic_mean")
    turnover = metrics.get("turnover")
    if _finite(net) and float(net) <= 0:
        reasons.append("net spread non-positive after costs")
    if _finite(ic) and float(ic) <= 0:
        reasons.append("IC non-positive")
    if _finite(turnover) and float(turnover) > 0.8:
        reasons.append("turnover high for long-only deployment")
    return reasons


def policy_warnings(
    metrics: dict[str, Any], policy: GatePolicy | None = None
) -> list[str]:
    """Warnings that never veto: capacity availability and sample-size level-2."""
    policy = policy or load_gate_policy()
    warnings: list[str] = []
    capacity = metrics.get("capacity_multiple")
    if not _finite(capacity):
        warnings.append("capacity estimate unavailable; capacity is informational only")
    elif float(capacity) <= 0:
        warnings.append("capacity multiple non-positive; capacity is informational only")
    try:
        n_dates = int(metrics.get("n_dates") or 0)
    except (TypeError, ValueError):
        n_dates = 0
    if (
        evidence_status(metrics, policy) != "insufficient_evidence"
        and n_dates < int(policy.min_dates_warning)
    ):
        warnings.append(
            f"sample size {n_dates} below preferred {int(policy.min_dates_warning)}; evidence may be weak"
        )
    return warnings


def explain_result(
    result: dict[str, Any], policy: GatePolicy | None = None
) -> dict[str, Any]:
    """Deterministic Evaluator output; never changes gate verdicts.

    Capacity is informational only: it is reported as a warning, not a veto.
    """
    policy = policy or load_gate_policy()
    metrics = result.get("metrics") or {}
    status = evidence_status(metrics, policy)
    warnings = policy_warnings(metrics, policy)
    if status == "insufficient_evidence":
        missing = [name for name in REQUIRED_METRICS if not _finite(metrics.get(name))]
        reasons = [
            "insufficient evidence: "
            + (
                "missing/non-finite " + ", ".join(missing)
                if missing
                else f"n_dates < {int(policy.min_dates_insufficient)}"
            )
        ]
        next_action = "collect_more_evidence"
    elif status == "fail":
        reasons = _failure_reasons(metrics)
        next_action = "record_failure"
    else:
        reasons = []
        next_action = "validate_deployability"
    return {
        "schema": "factor_lab.alpha.evaluation.v1",
        "factor_ref": result.get("factor_ref") or {},
        "verdict": status,
        "reasons": reasons,
        "warnings": warnings,
        "caveats": [
            "long-short metrics are research evidence, not deployable returns",
            "long-only deployability requires a separate deployment gate",
            "capacity is reported for information and does not veto a candidate",
        ],
        "next_action": next_action,
    }


def edge_card_from_result(
    result: dict[str, Any],
    *,
    status: str = "research",
    source: str = "research_runtime",
    edge_id: str | None = None,
) -> dict[str, Any]:
    metrics = result.get("metrics") or {}
    card = make_edge_card(
        str(result.get("expression") or ""),
        metrics,
        status=status,
        mechanism="runtime_candidate",
        data_axes=(result.get("data_manifest") or {}).get("fields", []),
        horizon=int(result.get("horizon") or 5),
        cost_bps=float(result.get("cost_bps") or 20.0),
        source=source,
        validation={"mode": metrics.get("entry_rule")},
        evidence=[str(result.get("schema") or RESULT_SCHEMA)],
        research_mode="validate",
    )
    if edge_id:
        card["edge_id"] = edge_id
    return card
