"""Deterministic research runtime and evaluator for Alpha artifacts."""

from __future__ import annotations

from typing import Any

from factor_lab.alpha.data import AlphaDataset
from factor_lab.alpha.evaluation import evaluate_expression
from factor_lab.alpha.memory import make_edge_card


RESULT_SCHEMA = "factor_lab.alpha.research_result.v1"


def run_research(
    expression: str,
    dataset: AlphaDataset,
    *,
    factor_ref: dict[str, Any] | None = None,
    horizon: int = 5,
    cost_bps: float = 20.0,
) -> dict[str, Any]:
    metrics = evaluate_expression(
        expression,
        dataset.context,
        horizon=horizon,
        cost_bps=cost_bps,
        start=dataset.start,
        end=dataset.end,
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


def explain_result(result: dict[str, Any]) -> dict[str, Any]:
    """Deterministic Evaluator output; never changes gate verdicts."""
    metrics = result.get("metrics") or {}
    net = metrics.get("net_spread")
    ic = metrics.get("ic_mean")
    turnover = metrics.get("turnover")
    capacity = metrics.get("capacity_multiple")
    reasons: list[str] = []
    if net is None:
        reasons.append("net_spread missing")
    elif float(net) <= 0:
        reasons.append("net spread non-positive after costs")
    if ic is None:
        reasons.append("IC missing")
    elif float(ic) <= 0:
        reasons.append("IC non-positive")
    if turnover is not None and float(turnover) > 0.8:
        reasons.append("turnover high for long-only deployment")
    if capacity is not None and float(capacity) < 2.0:
        reasons.append("capacity multiple below 2x")
    verdict = "pass" if not reasons else "fail"
    return {
        "schema": "factor_lab.alpha.evaluation.v1",
        "factor_ref": result.get("factor_ref") or {},
        "verdict": verdict,
        "reasons": reasons,
        "caveats": [
            "long-short metrics are research evidence, not deployable returns",
            "long-only deployability requires a separate deployment gate",
        ],
        "next_action": "validate_deployability" if verdict == "pass" else "record_failure",
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
