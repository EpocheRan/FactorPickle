"""Long-only deployability lens for Alpha candidates.

Research signal evidence uses long-short spreads.  Deployment is long-only, so
this module measures the long leg against an equal-weight eligible universe
with the same next-open entry and cost convention.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from factor_lab.alpha.compiler import AlphaContext, compile_expression


def _t_stat(values: pd.Series) -> float:
    clean = values.replace([np.inf, -np.inf], np.nan).dropna()
    if len(clean) < 3 or float(clean.std(ddof=1)) == 0.0:
        return float("nan")
    return float(clean.mean() / clean.std(ddof=1) * np.sqrt(len(clean)))


def evaluate_long_only(
    factor: pd.DataFrame,
    close: pd.DataFrame,
    *,
    horizon: int = 5,
    cost_bps: float = 20.0,
    open_: pd.DataFrame | None = None,
    top_fraction: float = 0.20,
    min_names: int = 5,
) -> dict[str, Any]:
    horizon = max(1, int(horizon))
    factor = factor.reindex(index=close.index, columns=close.columns).astype(float)
    factor = factor.replace([np.inf, -np.inf], np.nan)
    if open_ is not None:
        entry = open_.reindex(index=close.index, columns=close.columns).shift(-1)
        forward = close.shift(-horizon) / entry - 1.0
        entry_rule = "next_open"
    else:
        forward = close.shift(-horizon) / close - 1.0
        entry_rule = "same_close"
    excess_values: list[float] = []
    weights_by_date: dict[pd.Timestamp, pd.Series] = {}
    names_count: list[int] = []
    for date in list(factor.index[::horizon]):
        f = factor.loc[date]
        r = forward.loc[date]
        valid = pd.concat([f.rename("f"), r.rename("r")], axis=1).dropna()
        if len(valid) < max(min_names, 10):
            continue
        n_top = max(min_names, int(round(len(valid) * float(top_fraction))))
        n_top = min(n_top, len(valid) - 1)
        top = valid.nlargest(n_top, "f")
        excess = float(top["r"].mean() - valid["r"].mean())
        if not np.isfinite(excess):
            continue
        excess_values.append(excess)
        names_count.append(len(top))
        weights_by_date[date] = pd.Series(1.0 / max(1, len(top)), index=top.index, dtype=float)
    if not excess_values:
        raise ValueError("factor has no evaluable long-only cross-sections")
    gross = pd.Series(excess_values, dtype=float)
    turnover = 0.0
    if len(weights_by_date) >= 2:
        ordered = sorted(weights_by_date)
        changes = []
        for left, right in zip(ordered, ordered[1:]):
            lw = weights_by_date[left].reindex(close.columns).fillna(0.0)
            rw = weights_by_date[right].reindex(close.columns).fillna(0.0)
            changes.append(float((rw - lw).abs().sum()))
        turnover = float(np.mean(changes) / 2.0) if changes else 0.0
    cost_per_period = turnover * 2.0 * float(cost_bps) / 10000.0
    net = gross - cost_per_period
    annualization = float(252.0 / horizon)
    return {
        "schema": "factor_lab.alpha.deployability.v1",
        "entry_rule": entry_rule,
        "horizon": horizon,
        "cost_bps": float(cost_bps),
        "n_dates": int(len(gross)),
        "avg_names": float(np.mean(names_count)) if names_count else 0.0,
        "long_only_gross_excess_per_period": float(gross.mean()),
        "long_only_net_excess_per_period": float(net.mean()),
        "long_only_net_excess_annualized": float(net.mean() * annualization),
        "long_only_te_annualized": float(net.std(ddof=1) * np.sqrt(annualization)) if len(net) > 2 else float("nan"),
        "long_only_t": _t_stat(net),
        "turnover": float(turnover),
        "cost_per_period": float(cost_per_period),
        "positive_ratio": float((net > 0).mean()),
    }


def evaluate_long_only_expression(
    expression: str,
    context: AlphaContext,
    *,
    horizon: int = 5,
    cost_bps: float = 20.0,
    start: str | None = None,
    end: str | None = None,
    top_fraction: float = 0.20,
) -> dict[str, Any]:
    factor, report = compile_expression(expression, context)
    if start is not None or end is not None:
        factor = factor.loc[
            (factor.index >= pd.Timestamp(start) if start is not None else True)
            & (factor.index <= pd.Timestamp(end) if end is not None else True)
        ]
    open_ = context.fields.get("open")
    result = evaluate_long_only(
        factor,
        context.close.loc[factor.index],
        horizon=horizon,
        cost_bps=cost_bps,
        open_=open_.loc[factor.index] if open_ is not None else None,
        top_fraction=top_fraction,
    )
    result["expression"] = str(expression)
    result["validation"] = report.to_dict()
    return result
