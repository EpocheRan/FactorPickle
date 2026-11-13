"""One-shot holdout confirmation for a search result."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
from typing import Any

from factor_lab.alpha.compiler import compile_expression
from factor_lab.alpha.data import AlphaDataset, HoldoutWindow
from factor_lab.alpha.deployability import evaluate_long_only_expression
from factor_lab.alpha.evaluation import evaluate_expression
from factor_lab.alpha.freeze import latest_passed_freeze
from factor_lab.alpha.gates import alpha_gate_spec
from factor_lab.alpha.governance import assert_holdout_unused, record_holdout_use
from factor_lab.alpha.memory import AlphaMemory, make_edge_card
from factor_lab.alpha.red_team import red_team_review
from factor_lab.alpha.research_runtime import evidence_status, policy_warnings
from factor_lab.alpha.universe import UniversePolicy, universe_summary
from factor_lab.core import config
from factor_lab.core.store import read_json, write_json_atomic


def confirm_search(
    search_path: str | Path,
    dataset: AlphaDataset,
    *,
    horizon: int = 5,
    cost_bps: float = 20.0,
    holdout: HoldoutWindow | None = None,
    memory: AlphaMemory | None = None,
    universe: pd.DataFrame | None = None,
    universe_policy: UniversePolicy | None = None,
) -> dict[str, Any]:
    if holdout is None:
        raise ValueError("confirm requires the persisted locked holdout window")
    if not holdout.locked:
        raise ValueError("holdout is not locked")
    assert_holdout_unused()
    start = pd.Timestamp(dataset.start)
    end = pd.Timestamp(dataset.end)
    if start < pd.Timestamp(holdout.holdout_start) or end > pd.Timestamp(holdout.holdout_end):
        raise ValueError("confirmation dataset is not contained in the locked holdout")
    payload = read_json(Path(search_path))
    if not isinstance(payload, dict):
        raise ValueError(f"invalid search payload: {search_path}")
    top = payload.get("top") or []
    confirmed = [
        item for item in (payload.get("confirmed") or [])
        if item.get("red_team", {}).get("passed") and item.get("expression")
    ]
    if confirmed:
        candidate = max(confirmed, key=lambda item: float(item.get("score", -1e9)))
    elif top:
        candidate = top[0]
    else:
        raise ValueError("search payload has no top candidate")
    expression = str(candidate["expression"])
    freeze_decision = latest_passed_freeze(expression)
    if freeze_decision is None:
        raise ValueError(
            "holdout confirmation requires a passed freeze decision for "
            f"{expression!r}; run `alpha freeze` first (one-shot holdout is protected)"
        )
    metrics = evaluate_expression(
        expression,
        dataset.context,
        horizon=horizon,
        groups=5,
        cost_bps=cost_bps,
        start=dataset.start,
        end=dataset.end,
        universe=universe,
        spec=alpha_gate_spec(),
        use_tradability=True,
    )
    factor, _ = compile_expression(expression, dataset.context)
    factor = factor.loc[(factor.index >= dataset.start) & (factor.index <= dataset.end)]
    review = red_team_review(
        factor,
        dataset.context.close.loc[factor.index],
        metrics,
        horizon=horizon,
        open_=dataset.context.fields.get("open").loc[factor.index] if dataset.context.fields.get("open") is not None else None,
        cost_bps=cost_bps,
        permutations=40,
        universe=universe,
    )
    try:
        long_only = evaluate_long_only_expression(
            expression,
            dataset.context,
            horizon=horizon,
            cost_bps=cost_bps,
            start=dataset.start,
            end=dataset.end,
            universe=universe,
        )
    except ValueError as exc:
        long_only = {
            "schema": "factor_lab.alpha.deployability.v1",
            "status": "unavailable",
            "error": str(exc),
            "long_only_net_excess_annualized": None,
            "long_only_te_annualized": None,
            "turnover": None,
            "cost_bps": float(cost_bps),
        }
    metric_status = evidence_status(metrics)
    if metric_status == "insufficient_evidence":
        holdout_status = "insufficient_evidence"
    elif review.get("passed") and float(metrics.get("net_spread", -1.0)) > 0.0:
        holdout_status = "pass"
    else:
        holdout_status = "fail"
    card_status = {
        "pass": "signal_validated",
        "fail": "killed",
        "insufficient_evidence": "parked",
    }[holdout_status]
    result = {
        "schema": "factor_lab.alpha.confirmation.v1",
        "search_path": str(search_path),
        "selection": "best_red_team_passed" if confirmed else "top_score",
        "expression": expression,
        "metrics": metrics,
        "long_only": long_only,
        "red_team": review,
        "passed": holdout_status == "pass",
        "evidence_status": holdout_status,
        "evidence_warnings": policy_warnings(metrics),
        "dataset": dataset.data_manifest,
        "universe": universe_summary(universe, universe_policy),
        "freeze_decision": {k: freeze_decision.get(k) for k in ("passed", "family_id", "created_at", "path")},
    }
    try:
        from factor_lab.alpha.trial_sink import record_event

        record_event("holdout", {"expression": expression, "metrics": metrics, "search_path": str(search_path)})
    except Exception:  # noqa: BLE001
        pass
    record_holdout_use(
        expression=expression,
        campaign_id=str(payload.get("campaign_id") or ""),
        data_hash=str((dataset.data_manifest or {}).get("data_hash") or ""),
        window_start=str(holdout.holdout_start),
        window_end=str(holdout.holdout_end),
        freeze_path=str(freeze_decision.get("path") or ""),
    )
    out = config.LAB_ROOT / "alpha" / "confirm" / f"{Path(search_path).stem}.json"
    write_json_atomic(out, result)
    result["path"] = str(out)

    memory = memory or AlphaMemory()
    card = make_edge_card(
        expression,
        metrics,
        status=card_status,
        mechanism="holdout_confirmation",
        data_axes=dataset.data_manifest.get("fields", []),
        horizon=horizon,
        cost_bps=cost_bps,
        source="holdout_confirm",
        validation={},
        red_team=review,
        evidence=[str(out), f"holdout:{holdout.holdout_start}..{holdout.holdout_end}"],
        kill_criteria=["holdout failed", "long_short_t < 2"],
        research_mode="validate",
        deployability={
            "status": long_only.get("status", "measured"),
            "long_only_excess_after_tax": long_only.get("long_only_net_excess_annualized"),
            "long_only_te": long_only.get("long_only_te_annualized"),
            "alpha_per_turn": None,
            "turnover": long_only.get("turnover"),
            "cost_bps": long_only.get("cost_bps", float(cost_bps)),
            "error": long_only.get("error"),
        },
    )
    if universe is not None:
        card["universe"] = universe_summary(universe, universe_policy)
    memory.record_edge_card(card)
    result["memory"] = memory.finalize()
    return result
