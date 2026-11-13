"""Canonical replication evaluator.

Post-run replication/synthesis results must be reproducible from repository
source, not from ad-hoc inline scripts.  This module owns the deterministic
long-only/horizon/regime grid used by replication campaigns.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

import pandas as pd

from factor_lab.alpha.deployability import evaluate_long_only_expression
from factor_lab.alpha.gates import long_only_gate
from factor_lab.alpha.mandate import load_mandate


def evaluate_long_only_grid(
    expression: str,
    context: Any,
    *,
    start: str,
    end: str,
    universe: pd.DataFrame,
    size_universe: pd.DataFrame,
    horizons: Iterable[int] = (5, 20, 60),
    windows: Mapping[str, tuple[str, str]] | None = None,
) -> dict[str, Any]:
    long_only: dict[str, Any] = {}
    for horizon in horizons:
        long_only[str(horizon)] = {
            "full": evaluate_long_only_expression(expression, context, horizon=int(horizon), start=start, end=end, universe=universe),
            "size": evaluate_long_only_expression(expression, context, horizon=int(horizon), start=start, end=end, universe=size_universe),
        }
    regime = {
        name: evaluate_long_only_expression(expression, context, horizon=20, start=window_start, end=window_end, universe=size_universe)
        for name, (window_start, window_end) in (windows or {}).items()
    }
    mandate = load_mandate()
    promotion = long_only_gate(long_only["20"]["size"], mandate)
    return {
        "schema": "factor_lab.alpha.replication_grid.v1",
        "expression": expression,
        "evaluation_start": start,
        "evaluation_end": end,
        "horizons": [int(item) for item in horizons],
        "long_only": long_only,
        "regime_20d": regime,
        "promotion": promotion,
    }


def summarize_grid(grid: Mapping[str, Any]) -> dict[str, Any]:
    long_only = grid.get("long_only") or {}
    summary = {}
    for horizon, books in long_only.items():
        for book in ("full", "size"):
            metrics = books.get(book) or {}
            summary[f"{horizon}d_{book}"] = {
                key: metrics.get(key)
                for key in ("n_dates", "long_only_net_excess_annualized", "long_only_te_annualized", "long_only_t", "turnover")
            }
    return {
        "schema": "factor_lab.alpha.replication_summary.v1",
        "expression": grid.get("expression"),
        "promotion": grid.get("promotion"),
        "books": summary,
    }
