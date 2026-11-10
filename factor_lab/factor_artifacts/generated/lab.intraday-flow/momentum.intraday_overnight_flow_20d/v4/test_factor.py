"""Tests for the Lab factor momentum.intraday_overnight_flow_20d."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from factor import factor as FACTOR

WINDOW = 20
MIN_PERIODS = 10


def _synthetic(n: int = 80, seed: int = 7):
    rng = np.random.default_rng(seed)
    index = pd.date_range("2024-01-02", periods=n, freq="B")
    open_ = 100.0 + rng.normal(0.0, 1.0, n)
    close_ = open_ * (1.0 + rng.normal(0.0, 0.01, n))
    return pd.DataFrame({"open": open_, "close": close_}, index=index)


def _reference(frame: pd.DataFrame) -> pd.Series:
    prev_close = frame["close"].shift(1)
    overnight = frame["open"] / prev_close - 1.0
    intraday = frame["close"] / frame["open"] - 1.0
    return (intraday - overnight).rolling(WINDOW, min_periods=MIN_PERIODS).mean()


def test_normal_calculation_matches_reference():
    frame = _synthetic()
    out = FACTOR(frame)
    expected = _reference(frame)

    assert isinstance(out, pd.Series)
    assert out.index.equals(frame.index)
    assert out.dtype == np.float64
    pd.testing.assert_series_equal(out, expected, check_names=False, atol=1e-12, rtol=1e-12)
    assert out.notna().sum() == len(frame) - MIN_PERIODS


def test_constant_prices_give_known_ratio():
    frame = pd.DataFrame({"open": 100.0, "close": 101.0}, index=pd.RangeIndex(40))
    out = FACTOR(frame)

    spread = 101.0 / 100.0 - 100.0 / 101.0
    assert out.iloc[:MIN_PERIODS].isna().all()
    assert np.allclose(out.iloc[MIN_PERIODS:].to_numpy(), spread, atol=1e-12)
    assert not np.isinf(out.to_numpy()).any()


def test_short_history_below_min_periods_is_all_nan():
    frame = _synthetic(n=MIN_PERIODS - 1)
    out = FACTOR(frame)
    assert len(out) == len(frame)
    assert out.isna().all()


@pytest.mark.parametrize("dropped", ["open", "close"])
def test_missing_column_returns_all_nan(dropped):
    frame = _synthetic().drop(columns=[dropped])
    out = FACTOR(frame)
    assert len(out) == len(frame)
    assert out.index.equals(frame.index)
    assert out.isna().all()


def test_missing_all_fields_returns_all_nan():
    frame = pd.DataFrame({"volume": np.arange(30, dtype="float64")})
    out = FACTOR(frame)
    assert out.index.equals(frame.index)
    assert out.isna().all()


def test_empty_frame_returns_empty_series():
    frame = pd.DataFrame({"open": [], "close": []}, dtype="float64")
    out = FACTOR(frame)
    assert out.empty
    assert out.dtype == np.float64


def test_zero_prices_never_produce_inf():
    frame = _synthetic()
    frame.iloc[5, frame.columns.get_loc("open")] = 0.0
    frame.iloc[21, frame.columns.get_loc("close")] = 0.0
    frame.iloc[22, frame.columns.get_loc("close")] = -1.0

    out = FACTOR(frame)
    values = out.to_numpy()
    assert len(values) == len(frame)
    assert not np.isinf(values).any()
    assert not np.isnan(values).all()


def test_non_numeric_values_are_coerced_without_inf():
    frame = _synthetic().astype(object)
    frame.iloc[3, frame.columns.get_loc("open")] = "n/a"
    frame.iloc[4, frame.columns.get_loc("close")] = None
    frame.iloc[9, frame.columns.get_loc("open")] = np.nan

    out = FACTOR(frame)
    values = out.to_numpy()
    assert len(values) == len(frame)
    assert not np.isinf(values).any()


def test_wide_price_range_never_returns_inf():
    n = 60
    rng = np.random.default_rng(11)
    open_ = 10.0 ** rng.uniform(-8.0, 8.0, n)
    close_ = open_ * (1.0 + rng.normal(0.0, 0.05, n))
    frame = pd.DataFrame({"open": open_, "close": close_}, index=pd.RangeIndex(n))

    out = FACTOR(frame)
    assert np.isfinite(out.dropna().to_numpy()).all()
    assert not np.isinf(out.to_numpy()).any()


def test_input_frame_is_not_mutated():
    frame = _synthetic()
    snapshot = frame.copy(deep=True)
    FACTOR(frame)
    pd.testing.assert_frame_equal(frame, snapshot)
