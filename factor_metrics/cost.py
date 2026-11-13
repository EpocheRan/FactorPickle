"""Cost sensitivity (copied verbatim from research.report.metrics)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from .performance import _period_performance
from .quantile import QuantileResult, _quantile_nav


def _cost_sensitivity(
    factor: pd.DataFrame,
    close: pd.DataFrame,
    open_: pd.DataFrame,
    *,
    groups: int,
    horizon: int,
    tradeable: pd.DataFrame | None,
    quantile: QuantileResult | None = None,
) -> pd.DataFrame:
    # 分组和毛收益与 bps 无关；一次分组即可，后续只按换手派生不同成本。
    q = quantile or _quantile_nav(
        factor, close, open_, groups=groups, horizon=horizon, cost_bps=0.0,
        tradeable=tradeable,
    )
    turnover_df = q.turnover_df
    periods_gross = q.periods_gross
    q_hi = f"Q{groups}"
    gross_spread = (
        periods_gross.get(q_hi, pd.Series(dtype="float"))
        - periods_gross.get("Q1", pd.Series(dtype="float"))
    )
    legs_turnover = (
        turnover_df.get(q_hi, pd.Series(dtype="float"))
        + turnover_df.get("Q1", pd.Series(dtype="float"))
    )
    rows = {}
    for bps in (0.0, 10.0, 20.0, 30.0, 50.0):
        spread = (gross_spread - legs_turnover * bps / 10000.0).dropna()
        perf = _period_performance(spread, horizon)
        rows[f"{bps:.0f}bp"] = {
            "ann": perf.get("ann", np.nan),
            "sharpe": perf.get("sharpe", np.nan),
            "mdd": perf.get("mdd", np.nan),
        }
    return pd.DataFrame(rows).T
