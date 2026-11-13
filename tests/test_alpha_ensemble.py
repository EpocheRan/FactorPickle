"""Weak-signal ensemble construction and power diagnostics."""

from __future__ import annotations

import numpy as np
import pandas as pd

from factor_lab.alpha.compiler import AlphaContext
from factor_lab.alpha.ensemble import build_rank_composite, ensemble_power, evaluate_ensemble


def _context() -> AlphaContext:
    rng = np.random.default_rng(7)
    dates = pd.bdate_range("2022-01-03", periods=240)
    symbols = [f"{600000 + i:06d}" for i in range(20)]
    close = pd.DataFrame(100.0 * np.exp(np.cumsum(rng.normal(0.0002, 0.01, (len(dates), len(symbols))), axis=0)), index=dates, columns=symbols)
    fields = {
        "close": close,
        "open": close.shift(1).fillna(close),
        "amount": pd.DataFrame(rng.uniform(1e7, 1e9, close.shape), index=dates, columns=symbols),
        "returns": close.pct_change().fillna(0.0),
        "fin_revenue": pd.DataFrame(rng.uniform(1e8, 1e10, close.shape), index=dates, columns=symbols),
        "fin_total_assets": pd.DataFrame(rng.uniform(1e9, 1e11, close.shape), index=dates, columns=symbols),
        "fin_gross_profit": pd.DataFrame(rng.uniform(1e7, 1e9, close.shape), index=dates, columns=symbols),
        "holder_chg": pd.DataFrame(rng.normal(0, 1, close.shape), index=dates, columns=symbols),
        "float_mv": pd.DataFrame(rng.uniform(1e9, 1e11, close.shape), index=dates, columns=symbols),
    }
    return AlphaContext(close=close, fields=fields, panel=None, metadata={})


def test_weak_signal_composite_and_power() -> None:
    context = _context()
    components = {
        "a": "rank(div(fin_gross_profit, fin_total_assets))",
        "b": "neg(rank(holder_chg))",
        "c": "rank(div(ts_mean(abs(returns), 20), ts_mean(amount, 20)))",
    }
    composite, diagnostics = build_rank_composite(components, context)
    assert composite.shape == context.close.shape
    assert diagnostics["schema"] == "factor_lab.alpha.weak_signal_composite.v1"
    assert diagnostics["weights"] == {"a": 1 / 3, "b": 1 / 3, "c": 1 / 3}
    power = ensemble_power({"a": 1.2, "b": 1.4, "c": 1.1}, n_periods=100, mean_correlation=0.05)
    assert power["available"] is True
    assert 0.0 <= power["power_approx"] <= 1.0
    mask = pd.DataFrame(True, index=context.close.index, columns=context.close.columns)
    result = evaluate_ensemble(
        composite,
        context,
        start=str(context.close.index[100].date()),
        end=str(context.close.index[-1].date()),
        universe=mask,
        size_universe=mask,
        horizons=(20,),
        component_t_stats={"a": 1.2, "b": 1.4, "c": 1.1},
        mean_correlation=0.05,
    )
    assert result["schema"] == "factor_lab.alpha.weak_signal_ensemble_result.v1"
    assert "20" in result["long_only"]["size"]
    assert result["power"]["n_components"] == 3
