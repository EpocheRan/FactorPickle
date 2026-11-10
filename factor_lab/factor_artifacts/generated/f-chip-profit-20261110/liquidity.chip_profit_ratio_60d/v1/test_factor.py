"""Unit tests for liquidity.chip_profit_ratio_60d.

FactorLibrary runs these from the version directory:

    python -m pytest -q test_factor.py

so ``from factor import factor`` resolves to this version's factor.py.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from factor import factor

FACTOR_ID = "liquidity.chip_profit_ratio_60d"
WINDOW = 60
MIN_PERIODS = 40


def _index(n: int) -> pd.DatetimeIndex:
    return pd.date_range("2024-01-02", periods=n, freq="B")


def _random_frame(n: int = 140, seed: int = 7) -> pd.DataFrame:
    idx = _index(n)
    rng = np.random.default_rng(seed)
    close = 10.0 * np.exp(np.cumsum(rng.normal(0.0004, 0.02, n)))
    amount = rng.uniform(1e7, 5e8, n)
    return pd.DataFrame({"close": close, "amount": amount}, index=idx)


def _reference(close: np.ndarray, amount: np.ndarray, t: int) -> float:
    """Independent loop implementation of the spec formula at position t."""
    if t < 0 or not np.isfinite(close[t]) or close[t] <= 0.0:
        return np.nan
    numerator = 0.0
    denominator = 0.0
    count = 0
    for lag in range(1, WINDOW + 1):
        j = t - lag
        if j < 0:
            break
        c_j = close[j]
        a_j = amount[j]
        if not (np.isfinite(c_j) and np.isfinite(a_j)) or c_j <= 0.0 or a_j <= 0.0:
            continue
        count += 1
        denominator += a_j
        if close[t] > c_j:
            numerator += a_j
    if count < MIN_PERIODS or denominator <= 0.0:
        return np.nan
    return numerator / denominator


def test_normal_computation_matches_reference() -> None:
    frame = _random_frame()
    out = factor(frame)
    assert isinstance(out, pd.Series)
    assert out.index.equals(frame.index)
    assert out.dtype == np.float64
    assert out.name == FACTOR_ID
    assert np.isfinite(out.dropna()).all()

    close = frame["close"].to_numpy(dtype=float)
    amount = frame["amount"].to_numpy(dtype=float)
    expected = np.array([_reference(close, amount, t) for t in range(len(frame))])
    np.testing.assert_allclose(
        out.to_numpy(dtype=float), expected, rtol=1e-12, atol=1e-12, equal_nan=True
    )


def test_min_periods_boundary() -> None:
    out = factor(_random_frame(100))
    assert out.iloc[:MIN_PERIODS].isna().all()  # positions 0..39 have < 40 lags
    assert np.isfinite(out.iloc[MIN_PERIODS:].to_numpy(dtype=float)).all()

    # 40 rows of history can never reach 40 valid lags; 41 rows reach it once.
    assert factor(_random_frame(40)).isna().all()
    assert np.isfinite(factor(_random_frame(41)).iloc[-1])


def test_monotonic_rising_close_gives_one() -> None:
    idx = _index(100)
    rng = np.random.default_rng(11)
    close = pd.Series(np.linspace(10.0, 25.0, 100), index=idx)
    amount = pd.Series(rng.uniform(1e7, 2e8, 100), index=idx)
    out = factor(pd.DataFrame({"close": close, "amount": amount}, index=idx))
    assert out.iloc[:MIN_PERIODS].isna().all()
    np.testing.assert_allclose(out.iloc[MIN_PERIODS:].to_numpy(dtype=float), 1.0, rtol=0, atol=0)


def test_monotonic_falling_close_gives_zero() -> None:
    idx = _index(100)
    rng = np.random.default_rng(12)
    close = pd.Series(np.linspace(25.0, 10.0, 100), index=idx)
    amount = pd.Series(rng.uniform(1e7, 2e8, 100), index=idx)
    out = factor(pd.DataFrame({"close": close, "amount": amount}, index=idx))
    assert out.iloc[:MIN_PERIODS].isna().all()
    np.testing.assert_allclose(out.iloc[MIN_PERIODS:].to_numpy(dtype=float), 0.0, rtol=0, atol=0)


def test_output_bounded_in_unit_interval() -> None:
    for seed in range(5):
        frame = _random_frame(200, seed=seed)
        valid = factor(frame).dropna()
        assert not valid.empty
        assert (valid >= 0.0).all()
        assert (valid <= 1.0).all()


def test_missing_columns_returns_all_nan() -> None:
    frame = _random_frame(100)
    assert factor(frame[["close"]]).isna().all()
    assert factor(frame[["amount"]]).isna().all()
    assert factor(frame[["close"]].assign(open=1.0)).isna().all()

    empty = pd.DataFrame(columns=["close", "amount"])
    out = factor(empty)
    assert isinstance(out, pd.Series)
    assert out.empty

    not_a_frame = factor(None)  # type: ignore[arg-type]
    assert isinstance(not_a_frame, pd.Series)
    assert not_a_frame.empty


def test_invalid_values_do_not_produce_inf() -> None:
    frame = _random_frame(140)
    idx = frame.index
    frame.loc[idx[130], "amount"] = 0.0
    frame.loc[idx[131], "close"] = 0.0
    frame.loc[idx[132], "amount"] = -5.0
    frame.loc[idx[133], "close"] = -1.0
    frame.loc[idx[134], "amount"] = np.inf
    frame.loc[idx[135], "close"] = np.nan
    frame.loc[idx[136], "amount"] = np.nan
    frame.loc[idx[137], "close"] = np.inf

    out = factor(frame)
    assert not np.isinf(out.to_numpy(dtype=float)).any()
    assert np.isfinite(out.dropna()).all()

    # Invalid bars are skipped, not zero-weighted; the ratio stays bounded.
    valid = out.dropna()
    assert not valid.empty
    assert (valid >= 0.0).all()
    assert (valid <= 1.0).all()


def test_non_numeric_values_are_coerced() -> None:
    frame = _random_frame(140).astype(object)
    frame.iloc[100, 0] = "bad"
    frame.iloc[110, 1] = "n/a"
    out = factor(frame)
    assert not np.isinf(out.to_numpy(dtype=float)).any()
    assert np.isfinite(out.dropna()).all()


def test_input_is_not_mutated() -> None:
    frame = _random_frame(120)
    before = frame.copy(deep=True)
    factor(frame)
    pd.testing.assert_frame_equal(frame, before)

    object_frame = _random_frame(120).astype(object)
    object_frame.iloc[50, 0] = "bad"
    object_before = object_frame.copy(deep=True)
    factor(object_frame)
    pd.testing.assert_frame_equal(object_frame, object_before)
