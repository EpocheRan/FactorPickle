"""One-shot holdout confirmation for a search result."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
from typing import Any

from factor_lab.alpha.compiler import compile_expression
from factor_lab.alpha.data import AlphaDataset, HoldoutWindow
from factor_lab.alpha.evaluation import evaluate_expression
from factor_lab.alpha.red_team import red_team_review
from factor_lab.core import config
from factor_lab.core.store import read_json, write_json_atomic


def confirm_search(
    search_path: str | Path,
    dataset: AlphaDataset,
    *,
    horizon: int = 5,
    cost_bps: float = 20.0,
    holdout: HoldoutWindow | None = None,
) -> dict[str, Any]:
    if holdout is None:
        raise ValueError("confirm requires the persisted locked holdout window")
    if not holdout.locked:
        raise ValueError("holdout is not locked")
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
    metrics = evaluate_expression(
        expression,
        dataset.context,
        horizon=horizon,
        groups=5,
        cost_bps=cost_bps,
        start=dataset.start,
        end=dataset.end,
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
    )
    result = {
        "schema": "factor_lab.alpha.confirmation.v1",
        "search_path": str(search_path),
        "selection": "best_red_team_passed" if confirmed else "top_score",
        "expression": expression,
        "metrics": metrics,
        "red_team": review,
        "passed": bool(review.get("passed")) and float(metrics.get("net_spread", -1.0)) > 0.0,
        "dataset": dataset.data_manifest,
    }
    out = config.LAB_ROOT / "alpha" / "confirm" / f"{Path(search_path).stem}.json"
    write_json_atomic(out, result)
    result["path"] = str(out)
    return result
