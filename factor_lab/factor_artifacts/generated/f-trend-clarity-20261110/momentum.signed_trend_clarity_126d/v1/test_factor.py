"""Unit tests for momentum.signed_trend_clarity_126d.

FactorLibrary runs these from the version directory:

    python -m pytest -q test_factor.py

so ``from factor import factor`` resolves to this version's factor.py.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from factor import factor

FACTOR_ID = "momentum.signed_trend_clarity_126d"
WINDOW = 126


def _index(n: int) -> pd.DatetimeIndex:
    return pd.date_range("2023-01-02", periods=n, freq="B")


def _random_frame(n: int = 320, seed: int = 7) -> pd.DataFrame:
    idx = _index(n)
    rng = np.random.default_rng(seed)
    close = 10.0 * np.exp(np.cumsum(rng.normal(0.0003, 0.015, n)))
    return pd.DataFrame({"close": close}, index=idx)


def _reference(close: np.ndarray, t: int) -> float:
    """Independent per-window OLS/R^2 implementation of the spec formula."""
    if t < WINDOW - 1:
        return np.nan
    window = close[t - WINDOW + 1: t + 1]
    if not np.all(np.isfinite(window)) or not np.all(window > 0.0):
        return np.nan
    y = np.log(window)
    x = np.arange(WINDOW, dtype=float)
    y_mean = y.mean()
    ss_tot = np.sum((y - y_mean) ** 2)
    if not ss_tot > 0.0:
        return np.nan
    x_centered = x - x.mean()
    slope = float(x_centered @ (y - y_mean) / (x_centered @ x_centered))
    intercept = y_mean - slope * x.mean()
    fitted = intercept + slope * x
    ss_res = float(np.sum((y - fitted) ** 2))
    r_squared = min(1.0, max(0.0, 1.0 - ss_res / ss_tot))
    sign = 1.0 if slope > 0.0 else (-1.0 if slope < 0.0 else 0.0)
    return sign * r_squared


def test_normal_computation_matches_reference() -> None:
    frame = _random_frame()
    out = factor(frame)
    assert isinstance(out, pd.Series)
    assert out.index.equals(frame.index)
    assert out.dtype == np.float64
    assert out.name == FACTOR_ID
    assert np.isfinite(out.dropna()).all()

    close = frame["close"].to_numpy(dtype=float)
    expected = np.array([_reference(close, t) for t in range(len(frame))])
    np.testing.assert_allclose(
        out.to_numpy(dtype=float), expected, rtol=1e-8, atol=1e-10, equal_nan=True
    )


def test_warmup_and_min_window() -> None:
    out = factor(_random_frame(320))
    assert out.iloc[:WINDOW - 1].isna().all()  # first 125 rows have no full window
    assert np.isfinite(out.iloc[WINDOW - 1:].to_numpy(dtype=float)).all()

    assert factor(_random_frame(WINDOW - 1)).isna().all()
    only = factor(_random_frame(WINDOW))
    assert np.isnan(only.iloc[:-1].to_numpy(dtype=float)).all()
    assert np.isfinite(only.iloc[-1])


def test_flat_constant_prices_are_nan() -> None:
    # SStot ~ 0 -> NaN, never a spurious R^2.
    for level in (10.0, 123.45, 0.01):
        frame = pd.DataFrame({"close": np.full(300, level)}, index=_index(300))
        assert factor(frame).isna().all()


def test_perfect_trends_are_plus_minus_one() -> None:
    idx = _index(300)
    up = np.exp(np.linspace(0.0, 1.0, 300))  # ln(close) exactly linear in time
    down = np.exp(np.linspace(1.0, 0.0, 300))
    out_up = factor(pd.DataFrame({"close": up}, index=idx)).iloc[WINDOW - 1:]
    out_down = factor(pd.DataFrame({"close": down}, index=idx)).iloc[WINDOW - 1:]
    np.testing.assert_allclose(out_up.to_numpy(dtype=float), 1.0, rtol=0, atol=1e-12)
    np.testing.assert_allclose(out_down.to_numpy(dtype=float), -1.0, rtol=0, atol=1e-12)


def test_output_range_is_minus_one_to_one() -> None:
    for seed in range(5):
        frame = _random_frame(400, seed=seed)
        valid = factor(frame).dropna()
        assert not valid.empty
        assert (valid >= -1.0).all()
        assert (valid <= 1.0).all()


def test_missing_columns_returns_all_nan() -> None:
    frame = _random_frame(200)
    only_amount = frame.rename(columns={"close": "amount"})
    assert factor(only_amount).isna().all()

    empty = factor(pd.DataFrame(columns=["close"]))
    assert isinstance(empty, pd.Series) and empty.empty

    not_a_frame = factor(None)  # type: ignore[arg-type]
    assert isinstance(not_a_frame, pd.Series) and not_a_frame.empty


def test_zero_or_negative_prices_do_not_produce_inf() -> None:
    frame = _random_frame(320)
    idx = frame.index
    frame.loc[idx[40], "close"] = 0.0
    frame.loc[idx[41], "close"] = -5.0
    frame.loc[idx[42], "close"] = np.inf
    frame.loc[idx[43], "close"] = np.nan
    out = factor(frame)
    assert not np.isinf(out.to_numpy(dtype=float)).any()
    assert np.isfinite(out.dropna()).all()
    # every window containing any of the corrupted bars (rows 40..43) is NaN:
    # window ending at t covers rows t-125..t, so t runs from 40 to 43+125.
    first_bad, last_bad = 40, 43
    assert out.iloc[first_bad:last_bad + WINDOW].isna().all()
    # windows strictly after the corrupted block are valid again
    assert np.isfinite(out.iloc[last_bad + WINDOW:].to_numpy(dtype=float)).all()

    zeros = pd.DataFrame({"close": np.zeros(300)}, index=_index(300))
    assert factor(zeros).isna().all()


def test_non_numeric_values_are_coerced() -> None:
    frame = _random_frame(300).astype(object)
    frame.iloc[100, 0] = "bad"
    out = factor(frame)
    assert not np.isinf(out.to_numpy(dtype=float)).any()
    assert np.isfinite(out.dropna()).all()


def test_input_is_not_mutated() -> None:
    frame = _random_frame(300)
    before = frame.copy(deep=True)
    factor(frame)
    pd.testing.assert_frame_equal(frame, before)

    object_frame = _random_frame(300).astype(object)
    object_frame.iloc[10, 0] = "bad"
    object_before = object_frame.copy(deep=True)
    factor(object_frame)
    pd.testing.assert_frame_equal(object_frame, object_before)
