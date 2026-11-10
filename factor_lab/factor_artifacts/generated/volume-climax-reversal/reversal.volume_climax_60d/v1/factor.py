from __future__ import annotations

import numpy as np
import pandas as pd


def factor(frame: pd.DataFrame) -> pd.Series:
    """Return of the highest-amount day in 60d, scaled by 60d volatility."""
    if not {"amount", "close"}.issubset(frame.columns):
        return pd.Series(np.nan, index=frame.index, dtype=float)
    close = pd.to_numeric(frame["close"], errors="coerce")
    amount = pd.to_numeric(frame["amount"], errors="coerce")
    ret = close.pct_change(fill_method=None)
    valid = amount.gt(0) & ret.notna()
    best_amt = pd.Series(-np.inf, index=frame.index, dtype=float)
    best_ret = pd.Series(np.nan, index=frame.index, dtype=float)
    for lag in range(60):
        amount_lag = amount.shift(lag)
        ret_lag = ret.shift(lag)
        mask = amount_lag.gt(best_amt) & amount_lag.notna() & ret_lag.notna()
        best_amt = best_amt.where(~mask, amount_lag)
        best_ret = best_ret.where(~mask, ret_lag)
    sigma = ret.rolling(60, min_periods=40).std()
    count = valid.rolling(60, min_periods=40).sum()
    out = (best_ret / sigma).where(count >= 40)
    return out.replace([np.inf, -np.inf], np.nan).astype(float)
