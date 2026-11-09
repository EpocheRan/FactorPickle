from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from research.report import metrics
from research.report.benchmark_polars import _pandas_quantile_forward_spread
from research.report.metrics_polars import quantile_forward_spread, quantile_nav, rank_ic_frame


def _frames(seed: int = 11, rows: int = 80, cols: int = 40):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2022-01-03", periods=rows)
    assets = [f"S{i:02d}" for i in range(cols)]
    factor = pd.DataFrame(rng.normal(size=(rows, cols)), index=dates, columns=assets)
    # inject a few NaNs and ties
    factor.iloc[3, 2] = np.nan
    factor.iloc[5, :5] = 0.0
    forward = pd.DataFrame(rng.normal(scale=0.02, size=(rows, cols)), index=dates, columns=assets)
    return factor, forward


def test_polars_quantile_nav_parity():
    factor, _forward = _frames(rows=60, cols=40)
    rng = np.random.default_rng(23)
    close = pd.DataFrame(
        rng.lognormal(mean=2.0, sigma=0.02, size=factor.shape),
        index=factor.index,
        columns=factor.columns,
    )
    open_ = close.shift(1).fillna(close.iloc[0])
    expected = metrics._quantile_nav(
        factor, close, open_, groups=5, horizon=5, cost_bps=20.0
    )
    actual = quantile_nav(factor, close, open_, groups=5, horizon=5, cost_bps=20.0)
    for left, right in (
        (expected.periods_gross, actual.periods_gross),
        (expected.periods_net, actual.periods_net),
        (expected.turnover_df, actual.turnover_df),
    ):
        common_index = left.index.intersection(right.index)
        common_columns = left.columns.intersection(right.columns)
        np.testing.assert_allclose(
            left.loc[common_index, common_columns].to_numpy(),
            right.loc[common_index, common_columns].to_numpy(),
            equal_nan=True,
            rtol=0,
            atol=1e-12,
        )


def test_metrics_backend_default_auto_and_metadata():
    from research.report import metrics_backend

    assert metrics_backend.requested_backend() == "auto"
    assert metrics_backend.resolve_quantile_backend(100_000) == "polars"
    assert metrics_backend.resolve_quantile_backend(6_000_000) == "pandas"
    assert metrics_backend.engine_metadata((1167, 200))["rank_ic"] == "pandas"
    assert metrics_backend.engine_metadata((1167, 200))["quantile"] == "polars"
    assert metrics_backend.engine_metadata((1167, 5773))["quantile"] == "pandas"


def test_metrics_backend_auto_routes_small_panel_to_polars(monkeypatch):
    factor, _forward = _frames(rows=60, cols=40)
    rng = np.random.default_rng(23)
    close = pd.DataFrame(
        rng.lognormal(mean=2.0, sigma=0.02, size=factor.shape),
        index=factor.index,
        columns=factor.columns,
    )
    open_ = close.shift(1).fillna(close.iloc[0])

    monkeypatch.setenv("REPORT_METRICS_BACKEND", "pandas")
    expected = metrics._quantile_nav(factor, close, open_, groups=5, horizon=5, cost_bps=20.0)
    monkeypatch.setenv("REPORT_METRICS_BACKEND", "auto")
    actual = metrics._quantile_nav(factor, close, open_, groups=5, horizon=5, cost_bps=20.0)
    np.testing.assert_allclose(
        expected.periods_gross.to_numpy(),
        actual.periods_gross.to_numpy(),
        equal_nan=True,
        atol=1e-12,
        rtol=0,
    )


@pytest.mark.parametrize("min_names", [20, 25])
def test_polars_rank_ic_parity(min_names: int):
    factor, forward = _frames()
    pandas_ic = metrics._rank_ic_frame(factor, forward, min_names=min_names)
    polars_ic = rank_ic_frame(factor, forward, min_names=min_names)
    common = pandas_ic.index.intersection(polars_ic.index)
    assert len(common) == len(pandas_ic)
    np.testing.assert_allclose(
        pandas_ic.reindex(common).to_numpy(),
        polars_ic.reindex(common).to_numpy(),
        equal_nan=True,
        rtol=0,
        atol=1e-12,
    )


def test_polars_quantile_forward_spread_parity():
    factor, forward = _frames()
    pandas_spread = _pandas_quantile_forward_spread(factor, forward, groups=5)
    polars_spread = quantile_forward_spread(factor, forward, groups=5)
    common = pandas_spread.index.intersection(polars_spread.index)
    assert len(common) == len(pandas_spread)
    np.testing.assert_allclose(
        pandas_spread.reindex(common).to_numpy(),
        polars_spread.reindex(common).to_numpy(),
        equal_nan=True,
        rtol=0,
        atol=1e-12,
    )


def test_polars_module_is_optional_at_import():
    from research.report import metrics_polars

    assert metrics_polars is not None
