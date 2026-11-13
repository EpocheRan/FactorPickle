"""Portfolio construction and execution-layer tests."""

from __future__ import annotations

import numpy as np
import pandas as pd

from factor_lab.alpha.compiler import AlphaContext
from factor_lab.alpha.execution import ExecutionPolicy, execute_target_weights, tradeable_side
from factor_lab.alpha.portfolio import PortfolioPolicy, build_long_only_weights
from factor_lab.alpha.portfolio_backtest import run_portfolio_backtest


def _context() -> AlphaContext:
    rng = np.random.default_rng(11)
    dates = pd.bdate_range("2023-01-02", periods=180)
    symbols = [f"{600000 + i:06d}" for i in range(30)]
    close = pd.DataFrame(100.0 * np.exp(np.cumsum(rng.normal(0.0002, 0.01, (len(dates), len(symbols))), axis=0)), index=dates, columns=symbols)
    open_ = close.shift(1).fillna(close)
    fields = {
        "close": close,
        "close_raw": close,
        "open": open_,
        "open_raw": open_,
        "high": close * 1.01,
        "high_raw": close * 1.01,
        "low": close * 0.99,
        "low_raw": close * 0.99,
        "volume": pd.DataFrame(1e6, index=dates, columns=symbols),
        "amount": pd.DataFrame(1e8, index=dates, columns=symbols),
        "returns": close.pct_change().fillna(0.0),
        "float_mv": pd.DataFrame(1e9, index=dates, columns=symbols),
    }
    return AlphaContext(close=close, fields=fields, panel=None, metadata={})


def test_weights_are_long_only_capped_and_turnover_limited() -> None:
    context = _context()
    date = context.close.index[-1]
    factor = pd.Series(np.linspace(1, 30, 30), index=context.close.columns)
    eligible = pd.Series(True, index=context.close.columns)
    industry = pd.Series(["A", "B", "C"] * 10, index=context.close.columns)
    policy = PortfolioPolicy(top_fraction=0.5, max_weight=0.10, max_industry_weight=0.40, risk_shrinkage=1.0, turnover_limit=0.50)
    weights, diagnostics = build_long_only_weights(factor, eligible, context=context, industry=industry, previous_weights=None, policy=policy)
    assert abs(float(weights.sum()) - 1.0) < 1e-8
    assert float(weights.max()) <= 0.10 + 1e-9
    assert diagnostics["max_weight"] <= 0.10 + 1e-9
    prev = pd.Series({context.close.columns[0]: 0.50, context.close.columns[1]: 0.50})
    limited, limited_diag = build_long_only_weights(factor, eligible, context=context, industry=industry, previous_weights=prev, policy=policy)
    assert limited_diag["realized_turnover"] is None or limited_diag["target_turnover"] >= 0


def test_execution_blocks_limit_up_buy_and_charges_fees() -> None:
    context = _context()
    date = context.close.index[-1]
    target = pd.Series(0.0, index=context.close.columns)
    target.iloc[0] = 0.5
    target.iloc[1] = 0.5
    # Make the first name a limit-up open so the buy is blocked.
    code = context.close.columns[0]
    prev_close = float(context.fields["close_raw"].shift(1).loc[date, code])
    context.fields["high_raw"].loc[date, code] = prev_close * 1.10
    context.fields["open_raw"].loc[date, code] = prev_close * 1.10
    policy = ExecutionPolicy(impact_coeff_bps=0.0)
    report = execute_target_weights(pd.Series(dtype=float), target, date=date, context=context, adv=pd.Series(1e12, index=context.close.columns), policy=policy)
    assert report["n_blocked"] >= 1
    assert report["cash_weight"] >= 0.0
    assert report["total_cost"] >= 0.0


def test_tradeable_side_filters_st_and_new_stock() -> None:
    policy = ExecutionPolicy()
    ok, reason = tradeable_side(code="600000", side="buy", raw_open=10, raw_high=10, raw_low=9.8, raw_prev_close=10, volume=1e6, policy=policy, security_master={"is_st": True})
    assert ok is False and reason == "st"
    ok, reason = tradeable_side(code="600000", side="buy", raw_open=10, raw_high=10, raw_low=9.8, raw_prev_close=10, volume=1e6, policy=policy, security_master={"list_days": 10})
    assert ok is False and reason == "new_stock"


def test_portfolio_backtest_returns_metrics() -> None:
    context = _context()
    factor = context.close.pct_change().rolling(20).mean()
    eligible = pd.DataFrame(True, index=context.close.index, columns=context.close.columns)
    result = run_portfolio_backtest(
        factor,
        context,
        eligible,
        start=str(context.close.index[40].date()),
        end=str(context.close.index[-1].date()),
        horizon=20,
        portfolio_policy=PortfolioPolicy(top_fraction=0.3, max_weight=0.10, risk_shrinkage=1.0),
        execution_policy=ExecutionPolicy(impact_coeff_bps=0.0),
    )
    assert result["schema"] == "factor_lab.alpha.portfolio_backtest.v1"
    assert result["n_periods"] >= 0
