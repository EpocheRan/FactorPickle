from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from research.report import metrics
from research.report.benchmark_polars import _pandas_quantile_forward_spread
from research.report.metrics_polars import quantile_forward_spread, rank_ic_frame


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
