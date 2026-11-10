"""Lab factor: liquidity.chip_profit_ratio_60d.

Mechanism: amount-weighted share of the last 60 trading days whose close is
below today's close.  A lag k counts only if it was actually observed (finite
close and amount, both > 0), so stale / suspended / invalid bars are skipped
instead of being treated as zero weight.  The value is the fraction of recent
traded value whose holder basis is below the current price ("chip in profit").
High values mean the amount-weighted near-term cost basis is far below the
current price, i.e. more holders in profit and therefore more profit-taking /
crowding pressure; the spec declares direction = -1 for forward 20d returns.

Definition (t = last bar of the frame):
    F_t = sum_{k=1..60} amount_{t-k} * I(close_t > close_{t-k})
          / sum_{k=1..60} amount_{t-k}
Only lags with finite, strictly positive amount_{t-k} and close_{t-k} enter
either sum.  F_t is NaN unless close_t is finite and > 0 and at least
MIN_PERIODS = 40 valid lags are available inside the 60-bar window.

Contract: depends on OHLCV close/amount only, returns a float64 Series aligned
to ``frame.index``, never returns inf, and never raises on missing fields or on
non-numeric / zero / negative / non-finite prices and amounts.  The input frame
is never modified.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

FACTOR_ID = "liquidity.chip_profit_ratio_60d"
WINDOW = 60
MIN_PERIODS = 40
REQUIRED_FIELDS = ("close", "amount")


def _nan_series(index: pd.Index) -> pd.Series:
    """All-NaN float series aligned to ``index``."""
    return pd.Series(np.nan, index=index, dtype="float64", name=FACTOR_ID)


def _valid(series: pd.Series) -> pd.Series:
    """Keep only finite, strictly positive values; everything else becomes NaN."""
    return series.where(np.isfinite(series) & (series > 0.0))


def factor(frame: pd.DataFrame) -> pd.Series:
    """Amount-weighted fraction of the last 60 bars below today's close."""
    if not isinstance(frame, pd.DataFrame):
        return pd.Series(dtype="float64", name=FACTOR_ID)
    if frame.empty:
        return _nan_series(frame.index)
    if any(field not in frame.columns for field in REQUIRED_FIELDS):
        return _nan_series(frame.index)

    close_col = frame["close"]
    amount_col = frame["amount"]
    if not isinstance(close_col, pd.Series) or not isinstance(amount_col, pd.Series):
        # Duplicate column labels make the input ambiguous; refuse via all-NaN.
        return _nan_series(frame.index)

    close = _valid(pd.to_numeric(close_col, errors="coerce").astype("float64"))
    amount = _valid(pd.to_numeric(amount_col, errors="coerce").astype("float64"))

    close_values = close.to_numpy(dtype="float64", na_value=np.nan)
    amount_values = amount.to_numpy(dtype="float64", na_value=np.nan)
    current_ok = np.isfinite(close_values)  # close_t already > 0 after masking

    n = close_values.shape[0]
    numerator = np.zeros(n, dtype="float64")
    denominator = np.zeros(n, dtype="float64")
    counts = np.zeros(n, dtype="float64")
    current = close_values

    for lag in range(1, min(WINDOW, n - 1) + 1):
        amount_lag = np.full(n, np.nan, dtype="float64")
        close_lag = np.full(n, np.nan, dtype="float64")
        amount_lag[lag:] = amount_values[:-lag]
        close_lag[lag:] = close_values[:-lag]

        pair_ok = np.isfinite(amount_lag) & np.isfinite(close_lag)
        weights = np.where(pair_ok, amount_lag, 0.0)
        in_the_money = pair_ok & current_ok & (current > close_lag)

        numerator += np.where(in_the_money, weights, 0.0)
        denominator += weights
        counts += pair_ok.astype("float64")

    enough = (counts >= MIN_PERIODS) & current_ok & (denominator > 0.0)
    safe_denominator = np.where(denominator > 0.0, denominator, np.nan)
    values = np.where(enough, numerator / safe_denominator, np.nan)
    values = np.clip(values, 0.0, 1.0)
    values = np.where(np.isfinite(values), values, np.nan)

    return pd.Series(values, index=frame.index, dtype="float64", name=FACTOR_ID)
