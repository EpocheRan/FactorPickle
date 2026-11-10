"""Lab factor: momentum.near_52w_high_252d.

Mechanism: 52-week high anchoring.  Divide today's close by the highest close of
the trailing 252 trading days (the high-water mark, current bar included).  The
result is the fraction of the 52-week high at which the stock currently trades:
1.0 exactly at a new 52-week high and lower the further below the high-water
mark it is.  Proximity to the 52-week high is used as a positive momentum
signal (direction = +1) via anchoring / underreaction to good news.

Definition for the window ending at t (252 bars, t-251..t):
    NHP_t = close_t / max(close_{t-251}, ..., close_t)
A close enters the window max only if it is finite and > 0.  The factor is NaN
unless the full 252-bar window is available (so the first 251 bars are NaN) and
at least MIN_PERIODS = 200 valid closes are present inside that window; NaN is
also returned when close_t itself is invalid.  Because close_t is part of the
max whenever it is valid, the output is bounded in (0, 1].

Contract: depends on OHLCV close only; returns a float64 Series aligned to
``frame.index``; never returns inf; never raises on missing fields or on
non-numeric / zero / negative / non-finite prices; the input frame is never
modified.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

FACTOR_ID = "momentum.near_52w_high_252d"
WINDOW = 252
MIN_PERIODS = 200
REQUIRED_FIELDS = ("close",)


def _nan_series(index: pd.Index) -> pd.Series:
    """All-NaN float series aligned to ``index``."""
    return pd.Series(np.nan, index=index, dtype="float64", name=FACTOR_ID)


def factor(frame: pd.DataFrame) -> pd.Series:
    """Close divided by its 252-bar high-water mark (current bar included)."""
    if not isinstance(frame, pd.DataFrame):
        return pd.Series(dtype="float64", name=FACTOR_ID)
    if frame.empty or any(field not in frame.columns for field in REQUIRED_FIELDS):
        return _nan_series(frame.index)

    close_col = frame["close"]
    if not isinstance(close_col, pd.Series):
        # Duplicate column labels make the input ambiguous; refuse via all-NaN.
        return _nan_series(frame.index)

    close = pd.to_numeric(close_col, errors="coerce").astype("float64")
    clean = close.where(np.isfinite(close) & (close > 0.0))

    # min_periods counts only non-NaN observations, i.e. "at least 200 valid
    # closes inside the window"; invalid bars never enter the high-water mark.
    high = clean.rolling(WINDOW, min_periods=MIN_PERIODS).max()
    ratio = clean / high

    # The 252-bar reference point needs a full window: first 251 bars are NaN.
    full_window = np.arange(len(close)) >= WINDOW - 1
    out = ratio.where(full_window)
    out = out.replace([np.inf, -np.inf], np.nan)
    out = out.clip(upper=1.0)  # close_t is inside the max when valid => <= 1
    out = out.astype("float64")
    out.name = FACTOR_ID
    return out
