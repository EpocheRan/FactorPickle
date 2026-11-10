"""Unit tests for momentum.near_52w_high_252d.

FactorLibrary runs these from the version directory:

    python -m pytest -q test_factor.py

so ``from factor import factor`` resolves to this version's factor.py.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from factor import factor

FACTOR_ID = "momentum.near_52w_high_252d"
WINDOW = 252
MIN_PERIODS = 200


def _index(n: int) -> pd.DatetimeIndex:
    return pd.date_range("2022-01-03", periods=n, freq="B")


def _random_frame(n: int = 400, seed: int = 7) -> pd.DataFrame:
    idx = _index(n)
    rng = np.random.default_rng(seed)
    close = 10.0 * np.exp(np.cumsum(rng.normal(0.0002, 0.018, n)))
    return pd.DataFrame({"close": close}, index=idx)


def _reference(close: np.ndarray, t: int) -> float:
    """Independent per-window implementation of the spec formula."""
    if t < WINDOW - 1:
        return np.nan
    window = close[t - WINDOW + 1: t + 1]
    valid = np.isfinite(window) & (window > 0.0)
    if int(valid.sum()) < MIN_PERIODS:
        return np.nan
    current = window[-1]
    if not np.isfinite(current) or current <= 0.0:
        return np.nan
    return float(current / window[valid].max())


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
        out.to_numpy(dtype=float), expected, rtol=1e-12, atol=1e-15, equal_nan=True
    )


def test_first_251_rows_are_nan() -> None:
    out = factor(_random_frame(400))
    assert out.iloc[:WINDOW - 1].isna().all()  # positions 0..250
    assert np.isfinite(out.iloc[WINDOW - 1:].to_numpy(dtype=float)).all()

    assert factor(_random_frame(WINDOW - 1)).isna().all()
    only = factor(_random_frame(WINDOW))
    assert np.isnan(only.iloc[:-1].to_numpy(dtype=float)).all()
    assert np.isfinite(only.iloc[-1])


def test_constant_prices_give_one_without_inf() -> None:
    frame = pd.DataFrame({"close": np.full(320, 12.34)}, index=_index(320))
    out = factor(frame)
    assert out.iloc[:WINDOW - 1].isna().all()
    np.testing.assert_allclose(out.iloc[WINDOW - 1:].to_numpy(dtype=float), 1.0, rtol=0, atol=0)
    assert not np.isinf(out.to_numpy(dtype=float)).any()


def test_today_is_included_in_the_window_max() -> None:
    idx = _index(320)
    rising = np.exp(np.linspace(0.0, 1.0, 320))  # every bar is its own 252-bar high
    out_up = factor(pd.DataFrame({"close": rising}, index=idx)).iloc[WINDOW - 1:]
    np.testing.assert_allclose(out_up.to_numpy(dtype=float), 1.0, rtol=0, atol=1e-15)

    falling = np.exp(np.linspace(1.0, 0.0, 320))
    out_down = factor(pd.DataFrame({"close": falling}, index=idx)).iloc[WINDOW - 1:]
    down_values = out_down.to_numpy(dtype=float)
    assert np.isfinite(down_values).all()
    assert (down_values > 0.0).all()
    assert (down_values <= 1.0).all()
    # the high-water mark of a falling series is the first bar of each window
    np.testing.assert_allclose(
        down_values, np.exp(np.linspace(1.0, 0.0, 320))[WINDOW - 1:] / falling[:320 - (WINDOW - 1)],
        rtol=1e-12, atol=1e-15,
    )


def test_min_periods_valid_count() -> None:
    base = _random_frame(WINDOW)["close"].to_numpy(dtype=float).copy()

    # exactly 200 valid closes in the 252-bar window -> last row valid
    frame_200 = base.copy()
    frame_200[:52] = np.nan
    out_200 = factor(pd.DataFrame({"close": frame_200}, index=_index(WINDOW)))
    assert np.isfinite(out_200.iloc[-1])

    # 199 valid closes -> NaN
    frame_199 = base.copy()
    frame_199[:53] = np.nan
    out_199 = factor(pd.DataFrame({"close": frame_199}, index=_index(WINDOW)))
    assert np.isnan(out_199.iloc[-1])

    # zero / negative / inf bars do not count as valid observations either
    frame_invalid = base.copy()
    frame_invalid[:53] = 0.0
    frame_invalid[10] = -3.0
    frame_invalid[11] = np.inf
    out_invalid = factor(pd.DataFrame({"close": frame_invalid}, index=_index(WINDOW)))
    assert np.isnan(out_invalid.iloc[-1])


def test_output_range_is_zero_to_one() -> None:
    for seed in range(5):
        frame = _random_frame(500, seed=seed)
        valid = factor(frame).dropna()
        assert not valid.empty
        assert (valid > 0.0).all()
        assert (valid <= 1.0).all()


def test_zero_or_negative_prices_do_not_produce_inf() -> None:
    frame = _random_frame(400)
    idx = frame.index
    frame.loc[idx[300], "close"] = 0.0
    frame.loc[idx[301], "close"] = -5.0
    frame.loc[idx[302], "close"] = np.inf
    frame.loc[idx[303], "close"] = np.nan
    out = factor(frame)
    assert not np.isinf(out.to_numpy(dtype=float)).any()
    assert np.isfinite(out.dropna()).all()
    assert (out.dropna() > 0.0).all() and (out.dropna() <= 1.0).all()

    close = frame["close"].to_numpy(dtype=float)
    expected = np.array([_reference(close, t) for t in range(len(frame))])
    np.testing.assert_allclose(
        out.to_numpy(dtype=float), expected, rtol=1e-12, atol=1e-15, equal_nan=True
    )

    all_zero = pd.DataFrame({"close": np.zeros(320)}, index=_index(320))
    assert factor(all_zero).isna().all()
    all_negative = pd.DataFrame({"close": -np.ones(320)}, index=_index(320))
    assert factor(all_negative).isna().all()


def test_missing_columns_returns_all_nan() -> None:
    frame = _random_frame(300)
    only_amount = frame.rename(columns={"close": "amount"})
    assert factor(only_amount).isna().all()

    empty = factor(pd.DataFrame(columns=["close"]))
    assert isinstance(empty, pd.Series) and empty.empty

    not_a_frame = factor(None)  # type: ignore[arg-type]
    assert isinstance(not_a_frame, pd.Series) and not_a_frame.empty


def test_non_numeric_values_are_coerced() -> None:
    frame = _random_frame(400).astype(object)
    frame.iloc[320, 0] = "bad"
    out = factor(frame)
    assert not np.isinf(out.to_numpy(dtype=float)).any()
    assert np.isfinite(out.dropna()).all()


def test_input_is_not_mutated() -> None:
    frame = _random_frame(320)
    before = frame.copy(deep=True)
    factor(frame)
    pd.testing.assert_frame_equal(frame, before)

    object_frame = _random_frame(320).astype(object)
    object_frame.iloc[10, 0] = "bad"
    object_before = object_frame.copy(deep=True)
    factor(object_frame)
    pd.testing.assert_frame_equal(object_frame, object_before)
