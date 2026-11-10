"""Lab factor: momentum.signed_trend_clarity_126d.

Mechanism: over the last 126 trading days, regress y = ln(close) on the equally
spaced time index x = 0..125 by ordinary least squares.  R^2 measures how
cleanly the log price follows a straight line (trend clarity / low noise) and
the sign of the OLS slope carries the direction, so

    F = sign(b) * R^2  in [-1, 1]

is +1 for a very clean uptrend, -1 for a very clean downtrend, and near 0 for
no trend or an extremely noisy one.  A clean trend is assumed to persist
(direction = +1 for forward returns).

Definition for the window ending at t (i = 0..125):
    y_i = ln(close_{t-125+i}),  x_i = i
    b    = sum (x_i - x_bar)(y_i - y_bar) / sum (x_i - x_bar)^2
    R^2  = 1 - SS_res / SS_tot
    F    = sign(b) * R^2

Vectorisation: the 126-bar windows are produced with numpy
``sliding_window_view`` (a view, no Python loop over days) and the closed-form
OLS is evaluated for every window at once.  x is fixed and equally spaced, so
S_xx is a constant; after centring each window the per-window work reduces to
one 126-dim dot product and one einsum, and centring first keeps SS_tot well
conditioned.

Warmup / validity: the first 125 bars are NaN.  A window is NaN unless all 126
closes are finite and > 0 and SS_tot is meaningfully positive; constant or flat
log prices leave SS_tot at floating-point noise and are therefore NaN instead
of a spurious R^2.

Contract: depends on OHLCV close only; returns a float64 Series aligned to
``frame.index``; never returns inf; never raises on missing fields or on
non-numeric / zero / negative / non-finite prices; the input frame is never
modified.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

FACTOR_ID = "momentum.signed_trend_clarity_126d"
WINDOW = 126
MIN_PERIODS = 126
REQUIRED_FIELDS = ("close",)


def _nan_series(index: pd.Index) -> pd.Series:
    """All-NaN float series aligned to ``index``."""
    return pd.Series(np.nan, index=index, dtype="float64", name=FACTOR_ID)


def factor(frame: pd.DataFrame) -> pd.Series:
    """Signed OLS R^2 of ln(close) on time over the last 126 bars."""
    if not isinstance(frame, pd.DataFrame):
        return pd.Series(dtype="float64", name=FACTOR_ID)
    if frame.empty or any(field not in frame.columns for field in REQUIRED_FIELDS):
        return _nan_series(frame.index)

    close_col = frame["close"]
    if not isinstance(close_col, pd.Series):
        # Duplicate column labels make the input ambiguous; refuse via all-NaN.
        return _nan_series(frame.index)

    close = pd.to_numeric(close_col, errors="coerce").astype("float64")
    close = close.where(np.isfinite(close) & (close > 0.0))

    values = close.to_numpy(dtype="float64", na_value=np.nan)
    n = values.size
    result = np.full(n, np.nan, dtype="float64")
    if n < WINDOW:
        return pd.Series(result, index=frame.index, dtype="float64", name=FACTOR_ID)

    with np.errstate(divide="ignore", invalid="ignore"):
        log_close = np.log(values)  # invalid prices are NaN -> NaN, never inf

    windows = np.lib.stride_tricks.sliding_window_view(log_close, WINDOW)
    complete = ~np.isnan(windows).any(axis=1)  # all 126 closes valid

    # Zero-fill incomplete windows so the vectorised OLS emits no warnings;
    # those rows are masked out by ``complete`` below anyway.
    filled = np.where(np.isnan(windows), 0.0, windows)
    means = filled.mean(axis=1, keepdims=True)
    centered = filled - means

    time = np.arange(WINDOW, dtype="float64")
    x_centered = time - time.mean()
    s_xx = float(x_centered @ x_centered)  # constant for the fixed window

    s_tot = np.einsum("ij,ij->i", centered, centered)
    s_xy = centered @ x_centered

    finite_log = np.isfinite(log_close)
    scale = 1.0
    if finite_log.any():
        scale = max(1.0, float(np.abs(log_close[finite_log]).max()))
    s_tot_tol = WINDOW * np.finfo("float64").eps * scale * scale

    usable = complete & (s_tot > s_tot_tol)
    window_values = np.full(windows.shape[0], np.nan, dtype="float64")
    if usable.any():
        s_xy_used = s_xy[usable]
        s_tot_used = s_tot[usable]
        slope = s_xy_used / s_xx
        r_squared = np.clip((slope * slope) * s_xx / s_tot_used, 0.0, 1.0)
        sign = np.where(slope > 0.0, 1.0, np.where(slope < 0.0, -1.0, 0.0))
        window_values[usable] = sign * r_squared

    result[WINDOW - 1:] = window_values
    result[~np.isfinite(result)] = np.nan
    return pd.Series(result, index=frame.index, dtype="float64", name=FACTOR_ID)
