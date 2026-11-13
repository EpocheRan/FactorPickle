"""Deterministic operators for the Alpha DSL.

Every operator is vectorised over a date x symbol DataFrame.  Time-series
operators use trailing windows only; cross-sectional operators act on each
date across symbols.  There is no implicit shifting and no future data.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

import numpy as np
import pandas as pd

Frame = pd.DataFrame


def _min_periods(window: int) -> int:
    window = int(window)
    return max(1, min(window, window // 2 + 1))


def _frame(value: Any, like: Frame | None = None) -> Any:
    if isinstance(value, pd.Series):
        return value.to_frame()
    return value


def _ts(x: Frame, window: int, fn: Callable[..., Any]) -> Frame:
    return fn(x, int(window), min_periods=_min_periods(int(window)))


def _rolling_rank(x: Frame, window: int, *, min_periods: int) -> Frame:
    rolling = x.rolling(int(window), min_periods=min_periods)
    if hasattr(rolling, "rank"):
        try:
            return rolling.rank(pct=True)
        except Exception:  # pragma: no cover - old pandas fallback
            pass
    return rolling.apply(lambda values: _pct_rank(values), raw=True)


def _pct_rank(values: np.ndarray) -> float:
    arr = np.asarray(values, dtype=float)
    valid = arr[np.isfinite(arr)]
    current = arr[-1]
    if not np.isfinite(current) or not len(valid):
        return np.nan
    return float((valid < current).sum() + 0.5 * (valid == current).sum()) / len(valid)


def _cs(frame: Frame, fn: Callable[[Frame], Any]) -> Frame:
    return fn(frame)


def neg(x: Any) -> Any:
    return -x


def absolute(x: Any) -> Any:
    return abs(x)


def sign(x: Any) -> Any:
    return np.sign(x)


def log(x: Any) -> Any:
    return np.log(x.where(x > 0) if isinstance(x, Frame) else x)


def sqrt(x: Any) -> Any:
    return np.sqrt(x.where(x >= 0) if isinstance(x, Frame) else x)


def add(a: Any, b: Any) -> Any:
    return a + b


def sub(a: Any, b: Any) -> Any:
    return a - b


def mul(a: Any, b: Any) -> Any:
    return a * b


def div(a: Any, b: Any) -> Any:
    result = a / b
    if isinstance(result, Frame):
        return result.replace([np.inf, -np.inf], np.nan)
    return result


def clip(x: Any, lower: float, upper: float) -> Any:
    return x.clip(float(lower), float(upper))


def where(condition: Any, a: Any, b: Any) -> Any:
    if isinstance(condition, Frame):
        cond = condition.fillna(0.0).astype(bool)
        if not isinstance(a, Frame):
            a = condition * 0 + a
        if not isinstance(b, Frame):
            b = condition * 0 + b
        return a.where(cond, b)
    return a if bool(condition) else b


def ts_mean(x: Frame, window: int) -> Frame:
    return _ts(x, window, lambda f, w, **kw: f.rolling(w, **kw).mean())


def ts_sum(x: Frame, window: int) -> Frame:
    return _ts(x, window, lambda f, w, **kw: f.rolling(w, **kw).sum())


def ts_std(x: Frame, window: int) -> Frame:
    return _ts(x, window, lambda f, w, **kw: f.rolling(w, **kw).std())


def ts_max(x: Frame, window: int) -> Frame:
    return _ts(x, window, lambda f, w, **kw: f.rolling(w, **kw).max())


def ts_min(x: Frame, window: int) -> Frame:
    return _ts(x, window, lambda f, w, **kw: f.rolling(w, **kw).min())


def ts_delta(x: Frame, window: int) -> Frame:
    return x - x.shift(int(window))


def ts_pct(x: Frame, window: int) -> Frame:
    return div(x, x.shift(int(window))) - 1.0


def ts_rank(x: Frame, window: int) -> Frame:
    return _rolling_rank(x, int(window), min_periods=_min_periods(int(window)))


def ts_zscore(x: Frame, window: int) -> Frame:
    mean = ts_mean(x, window)
    std = ts_std(x, window)
    return div(x - mean, std)


def ts_decay(x: Frame, window: int) -> Frame:
    # Exponentially weighted trailing mean; span is the DSL's decay window.
    return x.ewm(span=max(2, int(window)), adjust=False, min_periods=_min_periods(int(window))).mean()


def ts_argmax(x: Frame, window: int) -> Frame:
    def _arg(values: np.ndarray) -> float:
        arr = np.asarray(values, dtype=float)
        if not np.isfinite(arr).any():
            return np.nan
        return float(np.nanargmax(arr) / max(1, len(arr) - 1))
    return x.rolling(int(window), min_periods=_min_periods(int(window))).apply(_arg, raw=True)


def ts_argmin(x: Frame, window: int) -> Frame:
    def _arg(values: np.ndarray) -> float:
        arr = np.asarray(values, dtype=float)
        if not np.isfinite(arr).any():
            return np.nan
        return float(np.nanargmin(arr) / max(1, len(arr) - 1))
    return x.rolling(int(window), min_periods=_min_periods(int(window))).apply(_arg, raw=True)


def ts_corr(a: Frame, b: Frame, window: int) -> Frame:
    if not isinstance(a, Frame):
        a = b * 0 + a
    if not isinstance(b, Frame):
        b = a * 0 + b
    return a.rolling(int(window), min_periods=_min_periods(int(window))).corr(b)


def ts_cov(a: Frame, b: Frame, window: int) -> Frame:
    if not isinstance(a, Frame):
        a = b * 0 + a
    if not isinstance(b, Frame):
        b = a * 0 + b
    return a.rolling(int(window), min_periods=_min_periods(int(window))).cov(b)


def ts_beta(y: Frame, x: Frame, window: int) -> Frame:
    return div(ts_cov(y, x, window), ts_cov(x, x, window))


def ts_residual(y: Frame, x: Frame, window: int) -> Frame:
    """Rolling regression residual of y on x (no intercept)."""
    beta = ts_beta(y, x, window)
    return y - beta * x


def ts_regression_slope(y: Frame, x: Frame, window: int) -> Frame:
    return ts_beta(y, x, window)


def ts_regression_r2(y: Frame, x: Frame, window: int) -> Frame:
    corr = ts_corr(y, x, window)
    return corr * corr


def ref(x: Frame, window: int) -> Frame:
    return x.shift(int(window))


def delay(x: Frame, window: int) -> Frame:
    return x.shift(int(window))


def cs_rank(x: Frame) -> Frame:
    return x.rank(axis=1, pct=True)


def cs_zscore(x: Frame) -> Frame:
    mean = x.mean(axis=1)
    std = x.std(axis=1).replace(0.0, np.nan)
    return x.sub(mean, axis=0).div(std, axis=0)


def cs_quantile(x: Frame) -> Frame:
    return x.rank(axis=1, pct=True)


def cs_scale(x: Frame) -> Frame:
    denom = x.abs().sum(axis=1).replace(0.0, np.nan)
    return x.div(denom, axis=0)


def cs_winsorize(x: Frame, lower: float = 0.01, upper: float = 0.99) -> Frame:
    lo = x.quantile(float(lower), axis=1)
    hi = x.quantile(float(upper), axis=1)
    return x.clip(lower=lo, upper=hi, axis=0)


@dataclass(frozen=True)
class OperatorSpec:
    fn: Callable[..., Any]
    min_args: int
    max_args: int
    kind: str = "time_series"
    description: str = ""


OPS: dict[str, OperatorSpec] = {
    "neg": OperatorSpec(neg, 1, 1, "scalar", "unary minus"),
    "abs": OperatorSpec(absolute, 1, 1, "scalar", "absolute value"),
    "sign": OperatorSpec(sign, 1, 1, "scalar", "sign"),
    "log": OperatorSpec(log, 1, 1, "scalar", "natural log for positive values"),
    "sqrt": OperatorSpec(sqrt, 1, 1, "scalar", "square root for non-negative values"),
    "add": OperatorSpec(add, 2, 2, "arithmetic", "addition"),
    "sub": OperatorSpec(sub, 2, 2, "arithmetic", "subtraction"),
    "mul": OperatorSpec(mul, 2, 2, "arithmetic", "multiplication"),
    "div": OperatorSpec(div, 2, 2, "arithmetic", "division, inf -> NaN"),
    "clip": OperatorSpec(clip, 3, 3, "scalar", "clip to lower/upper"),
    "where": OperatorSpec(where, 3, 3, "conditional", "where(cond, a, b)"),
    "ts_mean": OperatorSpec(ts_mean, 2, 2, "time_series", "trailing mean"),
    "ts_sum": OperatorSpec(ts_sum, 2, 2, "time_series", "trailing sum"),
    "ts_std": OperatorSpec(ts_std, 2, 2, "time_series", "trailing std"),
    "ts_max": OperatorSpec(ts_max, 2, 2, "time_series", "trailing max"),
    "ts_min": OperatorSpec(ts_min, 2, 2, "time_series", "trailing min"),
    "ts_delta": OperatorSpec(ts_delta, 2, 2, "time_series", "difference vs lag"),
    "ts_pct": OperatorSpec(ts_pct, 2, 2, "time_series", "percent change vs lag"),
    "ts_rank": OperatorSpec(ts_rank, 2, 2, "time_series", "trailing percentile rank"),
    "ts_zscore": OperatorSpec(ts_zscore, 2, 2, "time_series", "trailing z-score"),
    "ts_decay": OperatorSpec(ts_decay, 2, 2, "time_series", "trailing decayed mean"),
    "ts_argmax": OperatorSpec(ts_argmax, 2, 2, "time_series", "trailing argmax position"),
    "ts_argmin": OperatorSpec(ts_argmin, 2, 2, "time_series", "trailing argmin position"),
    "ts_corr": OperatorSpec(ts_corr, 3, 3, "time_series", "trailing correlation"),
    "ts_cov": OperatorSpec(ts_cov, 3, 3, "time_series", "trailing covariance"),
    "ts_beta": OperatorSpec(ts_beta, 3, 3, "time_series", "trailing regression beta"),
    "ts_residual": OperatorSpec(ts_residual, 3, 3, "time_series", "rolling residual of y on x"),
    "ts_regression_slope": OperatorSpec(ts_regression_slope, 3, 3, "time_series", "rolling slope"),
    "ts_regression_r2": OperatorSpec(ts_regression_r2, 3, 3, "time_series", "rolling R^2"),
    "ref": OperatorSpec(ref, 2, 2, "time_series", "lag by n bars"),
    "delay": OperatorSpec(delay, 2, 2, "time_series", "lag by n bars"),
    "rank": OperatorSpec(cs_rank, 1, 1, "cross_section", "cross-sectional percentile rank"),
    "zscore": OperatorSpec(cs_zscore, 1, 1, "cross_section", "cross-sectional z-score"),
    "quantile": OperatorSpec(cs_quantile, 1, 1, "cross_section", "cross-sectional quantile"),
    "scale": OperatorSpec(cs_scale, 1, 1, "cross_section", "L1 scale each cross-section"),
    "winsorize": OperatorSpec(cs_winsorize, 1, 3, "cross_section", "cross-sectional winsorize"),
}

OPERATOR_NAMES = tuple(sorted(OPS))
WINDOW_OPERATORS = frozenset(
    name for name, spec in OPS.items() if spec.kind == "time_series"
)
CROSS_SECTION_OPERATORS = frozenset(
    name for name, spec in OPS.items() if spec.kind == "cross_section"
)
FORBIDDEN_OPERATORS = frozenset({"shift", "future", "lookahead", "lead"})
