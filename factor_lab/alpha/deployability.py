"""Long-only deployability lens for Alpha candidates.

The research evidence is long-short; deployment is long-only.  The statistical
formulas (excess / TE / t / win, ``_ls_stats``) come from the calibrated
``factor_metrics`` core.  The Alpha product only defines the selection rule
(top fraction of the eligible cross-section) and the benchmark (equal-weight
eligible pool).
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from factor_metrics import ALPHA_GATE_SPEC, MetricsSpec, evaluate_long_only as _core_long_only
from factor_lab.alpha.compiler import AlphaContext, compile_expression
from factor_lab.alpha.gates import alpha_gate_spec


def evaluate_long_only(
    factor: pd.DataFrame,
    close: pd.DataFrame,
    *,
    horizon: int = 5,
    cost_bps: float = 20.0,
    open_: pd.DataFrame | None = None,
    top_fraction: float = 0.20,
    min_names: int = 5,
    universe: pd.DataFrame | None = None,
    tradability: pd.DataFrame | None = None,
    spec: MetricsSpec | None = None,
) -> dict[str, Any]:
    cost_spec = spec or alpha_gate_spec()
    if tradability is not None:
        eligible = (universe if universe is not None else pd.DataFrame(True, index=factor.index, columns=factor.columns))
        universe = eligible.reindex(index=tradability.index, columns=tradability.columns).fillna(False).astype(bool) & tradability.fillna(False).astype(bool)
    payload = _core_long_only(
        factor,
        close,
        open_=open_,
        horizon=horizon,
        cost_bps=cost_bps,
        top_fraction=top_fraction,
        min_names=min_names,
        universe=universe,
        spec=cost_spec,
        buy_fee_bps=float(cost_spec.cost.buy_fee_bps),
        sell_fee_bps=float(cost_spec.cost.sell_fee_bps),
    )
    return {"schema": "factor_lab.alpha.deployability.v1", **payload}


def evaluate_long_only_expression(
    expression: str,
    context: AlphaContext,
    *,
    horizon: int = 5,
    cost_bps: float = 20.0,
    start: str | None = None,
    end: str | None = None,
    top_fraction: float = 0.20,
    universe: pd.DataFrame | None = None,
    tradability: pd.DataFrame | None = None,
    spec: MetricsSpec | None = None,
) -> dict[str, Any]:
    factor, report = compile_expression(expression, context)
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
    open_ = context.fields.get("open")
    result = evaluate_long_only(
        factor,
        context.close.loc[factor.index],
        horizon=horizon,
        cost_bps=cost_bps,
        open_=open_.loc[factor.index] if open_ is not None else None,
        top_fraction=top_fraction,
        universe=universe,
        tradability=tradability,
        spec=spec,
    )
    result["expression"] = str(expression)
    result["validation"] = report.to_dict()
    return result
