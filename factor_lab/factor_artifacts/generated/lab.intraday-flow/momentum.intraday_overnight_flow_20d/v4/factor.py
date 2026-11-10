"""Lab factor: momentum.intraday_overnight_flow_20d.

Mechanism: a persistent deviation between the intraday return (close/open)
and the overnight return (open/prev_close) proxies for intraday buy/sell
absorption. The 20-day mean smooths the daily spread.

Contract: depends on OHLCV open/close only, returns a float ratio series
aligned to ``frame.index``, never returns inf, and never raises on missing
fields or degenerate prices (zero / negative / non-numeric -> NaN inputs).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

FACTOR_ID = "momentum.intraday_overnight_flow_20d"
WINDOW = 20
MIN_PERIODS = 10
REQUIRED_FIELDS = ("open", "close")


def _nan_series(index: pd.Index) -> pd.Series:
    """All-NaN float series aligned to ``index``."""
    return pd.Series(np.nan, index=index, dtype="float64", name=FACTOR_ID)


def factor(frame: pd.DataFrame) -> pd.Series:
    """20-day mean of (intraday return - overnight return)."""
    if not isinstance(frame, pd.DataFrame):
        return pd.Series(dtype="float64", name=FACTOR_ID)
    if frame.empty or any(field not in frame.columns for field in REQUIRED_FIELDS):
        return _nan_series(frame.index)

    open_ = pd.to_numeric(frame["open"], errors="coerce").astype("float64")
    close_ = pd.to_numeric(frame["close"], errors="coerce").astype("float64")

    # Guard every denominator: zero, negative or missing prices cannot
    # produce a meaningful ratio, so they become NaN instead of inf.
    prev_close = close_.shift(1)
    open_safe = open_.where(open_ > 0.0)
    close_safe = close_.where(close_ > 0.0)
    prev_close_safe = prev_close.where(prev_close > 0.0)

    overnight = open_safe / prev_close_safe - 1.0
    intraday = close_safe / open_safe - 1.0

    spread = (intraday - overnight).replace([np.inf, -np.inf], np.nan)
    out = spread.rolling(WINDOW, min_periods=MIN_PERIODS).mean()
    out = out.replace([np.inf, -np.inf], np.nan).astype("float64")
    out.name = FACTOR_ID
    return out
