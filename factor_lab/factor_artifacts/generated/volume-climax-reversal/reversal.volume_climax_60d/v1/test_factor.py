from __future__ import annotations

import numpy as np
import pandas as pd

from factor import factor


def _frame(n: int = 100) -> pd.DataFrame:
    idx = pd.date_range("2024-01-02", periods=n, freq="B")
    rng = np.random.default_rng(3)
    close = pd.Series(10.0 + rng.normal(0, 0.2, n).cumsum(), index=idx)
    amount = pd.Series(1e8 + rng.random(n) * 1e8, index=idx)
    amount.iloc[40] = 1e12
    close.iloc[40] = close.iloc[39] * 1.08
    return pd.DataFrame({"amount": amount, "close": close}, index=idx)


def test_computes_finite_series() -> None:
    out = factor(_frame())
    assert isinstance(out, pd.Series)
    assert out.index.equals(_frame().index)
    assert np.isfinite(out.dropna()).all()


def test_missing_columns_returns_nan() -> None:
    out = factor(pd.DataFrame({"close": np.linspace(10, 11, 30)}))
    assert out.isna().all()


def test_zero_amount_or_price_does_not_create_inf() -> None:
    frame = _frame()
    frame.iloc[-1, frame.columns.get_loc("amount")] = 0.0
    frame.iloc[-2, frame.columns.get_loc("close")] = 0.0
    out = factor(frame)
    assert not np.isinf(out.dropna()).any()
