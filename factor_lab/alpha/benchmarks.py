"""Phase 0 human-baseline benchmark."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pandas as pd

from factor_lab.alpha.baselines import HUMAN_BASELINES
from factor_lab.alpha.data import AlphaDataset, HoldoutWindow
from factor_lab.alpha.evaluation import evaluate_expression
from factor_lab.alpha.universe import UniversePolicy, universe_summary
from factor_lab.core import config
from factor_lab.core.store import write_json_atomic


def run_benchmark(
    dataset: AlphaDataset,
    *,
    holdout: HoldoutWindow | None = None,
    horizon: int = 5,
    cost_bps: float = 20.0,
    include_holdout: bool = False,
    out_path: str | None = None,
    universe: pd.DataFrame | None = None,
    universe_policy: UniversePolicy | None = None,
) -> dict[str, Any]:
    """Evaluate the public human corpus on development and (optionally) holdout.

    The holdout is *evaluated once* for benchmark reporting; it is never used to
    select a candidate or tune a parameter.
    """
    dev = dataset
    dev_start = holdout.dev_start if holdout else dataset.start
    dev_end = holdout.dev_end if holdout else dataset.end
    results = []
    for baseline in HUMAN_BASELINES:
        try:
            metrics = evaluate_expression(
                baseline.expression,
                dev.context,
                horizon=horizon,
                groups=5,
                cost_bps=cost_bps,
                start=dev_start,
                end=dev_end,
                universe=universe,
            )
            results.append(
                {
                    "name": baseline.name,
                    "family": baseline.family,
                    "source": baseline.source,
                    "expression": baseline.expression,
                    "metrics": metrics,
                }
            )
        except Exception as exc:  # noqa: BLE001
            results.append(
                {
                    "name": baseline.name,
                    "family": baseline.family,
                    "source": baseline.source,
                    "expression": baseline.expression,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )

    valid = [item["metrics"] for item in results if "metrics" in item]
    summary = {
        "n": len(results),
        "ic_mean": round(float(sum(item["ic_mean"] for item in valid) / max(1, len(valid))), 6),
        "net_spread": round(float(sum(item["net_spread"] for item in valid) / max(1, len(valid))), 6),
        "positive_net": sum(1 for item in valid if float(item.get("net_spread", 0.0)) > 0),
        "positive_ic": sum(1 for item in valid if float(item.get("ic_mean", 0.0)) > 0),
    }
    holdout_result = None
    if include_holdout and holdout is not None:
        hd = dataset
        holdout_result = []
        for baseline in HUMAN_BASELINES:
            try:
                metrics = evaluate_expression(
                    baseline.expression,
                    hd.context,
                    horizon=horizon,
                    groups=5,
                    cost_bps=cost_bps,
                    start=holdout.holdout_start,
                    end=holdout.holdout_end,
                    universe=universe,
                )
                holdout_result.append({"name": baseline.name, "family": baseline.family, "metrics": metrics})
            except Exception as exc:  # noqa: BLE001
                holdout_result.append({"name": baseline.name, "family": baseline.family, "error": str(exc)})

    payload = {
        "schema": "factor_lab.alpha.benchmark.v1",
        "created_at": config.now_iso(),
        "benchmark_id": f"BM-{uuid.uuid4().hex[:8]}",
        "dataset": dataset.data_manifest,
        "holdout_window": holdout.to_dict() if holdout else None,
        "horizon": horizon,
        "cost_bps": cost_bps,
        "universe": universe_summary(universe, universe_policy),
        "summary": summary,
        "baselines": results,
        "holdout_metrics": holdout_result,
    }
    if out_path is None:
        out_path = str(config.LAB_ROOT / "alpha" / "benchmark" / f"{payload['benchmark_id']}.json")
    write_json_atomic(Path(out_path), payload)
    return payload
