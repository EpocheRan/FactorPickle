"""Optional Polars backend for the report metrics hot path.

This is intentionally additive: the production pandas path in ``metrics.py``
stays the default.  The first prototype covers:

- cross-sectional rank IC
- forward-return quantile spread (Q_top - Q_bottom)

It converts wide factor/forward frames to Polars long form once and returns
pandas results, so callers can compare against ``metrics.py`` without changing
the ``FactorReport`` contract.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

try:
    import polars as pl
except Exception:  # pragma: no cover - optional dependency
    pl = None  # type: ignore[assignment]


class PolarsUnavailable(RuntimeError):
    pass


def _require_polars() -> Any:
    if pl is None:
        raise PolarsUnavailable("polars is not installed; use the pandas backend")
    return pl


def _wide_to_long(frame: pd.DataFrame, value_name: str) -> Any:
    polars = _require_polars()
    data = frame.reset_index()
    date_col = str(data.columns[0])
    data = data.rename(columns={date_col: "date"})
    return polars.from_pandas(data).unpivot(
        index="date",
        variable_name="asset",
        value_name=value_name,
    )


def rank_ic_frame(
    factor: pd.DataFrame,
    forward: pd.DataFrame,
    *,
    min_names: int = 20,
) -> pd.Series:
    """Polars implementation of ``metrics._rank_ic_frame``.

    Rank each side independently before joining, matching the production
    implementation (which ranks the full common columns and masks afterwards).
    """

    polars = _require_polars()
    common = factor.columns.intersection(forward.columns)
    if len(common) == 0:
        return pd.Series(dtype="float64")
    left = _wide_to_long(factor[common].reindex(index=factor.index), "f")
    right = _wide_to_long(forward[common].reindex(index=factor.index), "r")
    left = left.with_columns(polars.col("f").rank("average").over("date").alias("rf"))
    right = right.with_columns(polars.col("r").rank("average").over("date").alias("rr"))
    joined = left.select(["date", "asset", "rf"]).join(
        right.select(["date", "asset", "rr"]), on=["date", "asset"], how="inner"
    )
    joined = joined.filter(
        polars.col("rf").is_not_null() & polars.col("rr").is_not_null()
    )
    result = (
        joined.group_by("date")
        .agg(
            [
                polars.corr("rf", "rr").alias("ic"),
                polars.len().alias("n"),
            ]
        )
        .filter(polars.col("n") >= int(min_names))
        .sort("date")
    )
    if result.is_empty():
        return pd.Series(dtype="float64")
    frame = result.to_pandas()
    frame["date"] = pd.to_datetime(frame["date"]).dt.tz_localize(None).dt.normalize()
    series = pd.Series(frame["ic"].to_numpy(dtype="float64"), index=pd.DatetimeIndex(frame["date"]))
    # Production _rank_ic_frame keeps every factor date (NaN when degenerate).
    series = series.reindex(factor.index)
    return series.replace([np.inf, -np.inf], np.nan)


def quantile_forward_spread(
    factor: pd.DataFrame,
    forward: pd.DataFrame,
    *,
    groups: int = 5,
    min_names: int | None = None,
) -> pd.Series:
    """Q_top mean forward return - Q_bottom mean forward return, per date."""

    polars = _require_polars()
    common = factor.columns.intersection(forward.columns)
    if len(common) == 0:
        return pd.Series(dtype="float64")
    left = _wide_to_long(factor[common], "f")
    right = _wide_to_long(forward[common], "r")
    joined = left.join(right, on=["date", "asset"], how="inner").filter(
        polars.col("f").is_not_null() & polars.col("r").is_not_null()
    )
    # pandas rank(method="first") follows the factor column order; preserve it
    # so the bucket assignment is bit-for-bit comparable.
    asset_order = {str(asset): index for index, asset in enumerate(common)}
    joined = joined.with_columns(
        polars.col("asset").replace_strict(asset_order, default=len(asset_order)).alias("asset_order")
    ).sort(["date", "asset_order"])
    minimum = int(min_names if min_names is not None else groups * 5)
    joined = joined.with_columns(
        [
            polars.col("f").rank("ordinal").over("date").alias("rank"),
            polars.len().over("date").alias("n"),
        ]
    ).filter(polars.col("n") >= minimum)
    # pandas.qcut on ranks 1..n: bucket = number of quantile edges strictly
    # below rank; searchsorted(edges, rank, side="left").
    bucket_expr = polars.lit(0)
    for index in range(1, int(groups)):
        edge = 1.0 + (polars.col("n") - 1.0) * index / int(groups)
        bucket_expr = bucket_expr + (polars.col("rank") > edge).cast(polars.Int64)
    joined = joined.with_columns(bucket_expr.alias("q"))
    means = (
        joined.group_by(["date", "q"])
        .agg(polars.col("r").mean().alias("mean_r"))
        .sort(["date", "q"])
    )
    if means.is_empty():
        return pd.Series(dtype="float64")
    top = means.filter(polars.col("q") == groups - 1).select(["date", "mean_r"])
    bottom = means.filter(polars.col("q") == 0).select(["date", "mean_r"])
    spread = top.join(bottom, on="date", how="inner", suffix="_lo").select(
        ["date", (polars.col("mean_r") - polars.col("mean_r_lo")).alias("spread")]
    )
    frame = spread.to_pandas()
    frame["date"] = pd.to_datetime(frame["date"]).dt.tz_localize(None).dt.normalize()
    return pd.Series(frame["spread"].to_numpy(dtype="float64"), index=pd.DatetimeIndex(frame["date"]))
