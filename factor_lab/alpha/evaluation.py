"""Alpha evaluator: thin adapter over the research-calibrated ``factor_metrics`` core.

Every numerical algorithm now lives in :mod:`factor_metrics` and was extracted
verbatim from ``research/report/metrics.py`` (the calibrated source of truth).
This module only supplies the Alpha product contract:

- the ``AlphaMetrics`` payload schema (kept for downstream consumers),
- the Alpha gate profile (non-overlapping rebalance sampling, plain t),
- expression compilation and the PIT-universe mask.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from factor_metrics import (
    ALPHA_GATE_SPEC,
    MetricsSpec,
    evaluate_factor as _core_evaluate_factor,
)
from factor_lab.alpha.compiler import AlphaContext, compile_expression
from factor_lab.alpha.tradability import build_tradability_mask


def evaluate_factor(
    factor: pd.DataFrame,
    close: pd.DataFrame,
    *,
    horizon: int = 5,
    groups: int = 5,
    cost_bps: float = 20.0,
    amount: pd.DataFrame | None = None,
    open_: pd.DataFrame | None = None,
    min_names: int | None = None,
    universe: pd.DataFrame | None = None,
    spec: MetricsSpec | None = None,
    tradeable: pd.DataFrame | None = None,
) -> dict[str, Any]:
    """Evaluate a factor matrix with the calibrated research algorithms.

    The returned dict keeps the historical ``AlphaMetrics`` keys and adds
    ``metrics_spec`` (profile version + configuration hash) for auditability.
    """
    result = _core_evaluate_factor(
        factor,
        close,
        open_=open_,
        amount=amount,
        horizon=horizon,
        groups=groups,
        cost_bps=cost_bps,
        universe=universe,
        spec=spec or ALPHA_GATE_SPEC,
        min_ic_names=min_names,
        tradeable=tradeable,
    )
    return result.to_dict()


def evaluate_expression(
    expression: str,
    context: AlphaContext,
    *,
    horizon: int = 5,
    groups: int = 5,
    cost_bps: float = 20.0,
    start: Any | None = None,
    end: Any | None = None,
    universe: pd.DataFrame | None = None,
    spec: MetricsSpec | None = None,
    tradeable: pd.DataFrame | None = None,
    use_tradability: bool = False,
) -> dict[str, Any]:
    factor, report = compile_expression(expression, context)
    if use_tradability and tradeable is None:
        tradeable = build_tradability_mask(context)
    if start is not None or end is not None:
        factor = factor.loc[
            (factor.index >= pd.Timestamp(start) if start is not None else True)
            & (factor.index <= pd.Timestamp(end) if end is not None else True)
        ]
    if universe is not None:
        universe = (
            universe.reindex(index=factor.index, columns=factor.columns)
            .fillna(False)
            .astype(bool)
        )
    amount = context.fields.get("amount")
    open_ = context.fields.get("open")
    result = evaluate_factor(
        factor,
        context.close.loc[factor.index],
        horizon=horizon,
        groups=groups,
        cost_bps=cost_bps,
        amount=amount.loc[factor.index] if amount is not None else None,
        open_=open_.loc[factor.index] if open_ is not None else None,
        universe=universe,
        spec=spec,
        tradeable=tradeable.loc[factor.index] if tradeable is not None else None,
    )
    if tradeable is not None:
        result["tradability_blocked_fraction"] = float(1.0 - tradeable.loc[factor.index].to_numpy(dtype=bool).mean())
    result["expression"] = str(expression)
    result["validation"] = report.to_dict()
    return result
