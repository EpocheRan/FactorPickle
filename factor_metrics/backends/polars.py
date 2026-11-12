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
    """Polars implementation of :func:`factor_metrics.ic._rank_ic_frame`.

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


def quantile_nav(
    factor: pd.DataFrame,
    close: pd.DataFrame,
    open_: pd.DataFrame,
    *,
    groups: int,
    horizon: int,
    cost_bps: float = 20.0,
    tradeable: pd.DataFrame | None = None,
):
    """Polars membership/rank + NumPy holding-return engine.

    Returns the same ``QuantileResult`` contract as
    ``factor_metrics.quantile._quantile_nav``.
    """

    from ..quantile import QuantileResult

    polars = _require_polars()
    common = factor.columns.intersection(close.columns).intersection(open_.columns)
    if len(common) == 0:
        raise ValueError("factor/close/open have no common assets")
    f = factor[common]
    c = close[common].reindex(index=f.index)
    o = open_[common].reindex(index=f.index)
    dates = f.index
    rebalance = []
    i = 0
    while i + horizon + 1 < len(dates):
        rebalance.append(dates[i])
        i += horizon
    valid = f.notna()
    if tradeable is not None:
        trade_mask = tradeable.reindex(index=f.index, columns=common).fillna(False)
        valid = valid & trade_mask
    effective = f.where(valid)

    data = effective.reset_index().rename(columns={effective.index.name or "index": "date"})
    long = polars.from_pandas(data).unpivot(
        index="date", variable_name="asset", value_name="f"
    )
    long = long.filter(polars.col("f").is_not_null())
    if rebalance:
        long = long.filter(polars.col("date").is_in([pd.Timestamp(item) for item in rebalance]))
    if long.is_empty():
        empty = pd.DataFrame(index=dates or pd.DatetimeIndex([]))
        empty_turn = pd.DataFrame(index=pd.DatetimeIndex([]))
        return QuantileResult(
            nav=empty.copy(),
            turnover_df=empty_turn,
            periods_net=empty_turn.copy(),
            periods_gross=empty_turn.copy(),
            daily=empty.copy(),
            ls_cost=pd.Series(dtype="float64"),
            turnover_mean=pd.Series(dtype="float64"),
        )
    asset_order = {str(asset): index for index, asset in enumerate(common)}
    long = long.with_columns(
        polars.col("asset").replace_strict(asset_order, default=len(asset_order)).alias("asset_order")
    ).sort(["date", "asset_order"])
    long = long.with_columns(
        [
            polars.col("f").rank("ordinal").over("date").alias("rank"),
            polars.len().over("date").alias("n"),
        ]
    )
    bucket_expr = polars.lit(0)
    for index in range(1, int(groups)):
        edge = 1.0 + (polars.col("n") - 1.0) * index / int(groups)
        bucket_expr = bucket_expr + (polars.col("rank") > edge).cast(polars.Int64)
    long = long.with_columns(bucket_expr.alias("q"))
    grouped = long.group_by(["date", "q"]).agg(polars.col("asset")).sort(["date", "q"])
    membership: dict[pd.Timestamp, dict[int, list[str]]] = {}
    for row in grouped.iter_rows(named=True):
        timestamp = pd.Timestamp(row["date"])
        membership.setdefault(timestamp, {})[int(row["q"])] = list(row["asset"] or [])

    labels = [f"Q{index + 1}" for index in range(groups)]
    nav = pd.DataFrame(np.nan, index=dates, columns=labels)
    daily = pd.DataFrame(0.0, index=dates, columns=labels)
    ls_cost = pd.Series(0.0, index=dates)
    periods_net: list[dict[str, float]] = []
    periods_gross: list[dict[str, float]] = []
    period_dates: list[pd.Timestamp] = []
    turnover_rows: list[dict[str, float]] = []
    prev_members: dict[int, set[str]] = {group: set() for group in range(groups)}
    level = {group: 1.0 for group in range(groups)}
    close_np = c.to_numpy(dtype="float64")
    open_np = o.to_numpy(dtype="float64")
    col_pos = {str(code): position for position, code in enumerate(common)}

    for rebalance_index in range(0, len(dates), horizon):
        if rebalance_index + horizon + 1 >= len(dates):
            break
        dt = dates[rebalance_index]
        day_members = membership.get(dt)
        if not day_members:
            continue
        members = {group: list(day_members.get(group, [])) for group in range(groups)}
        if sum(len(items) for items in members.values()) < groups * 5:
            continue
        turnover = {}
        for group in range(groups):
            current = set(members[group])
            turnover[group] = len(current - prev_members[group]) / max(1, len(current))
            prev_members[group] = current
        turnover_rows.append({f"Q{group + 1}": turnover[group] for group in range(groups)})
        period_gross = {group: 1.0 for group in range(groups)}
        period_net = {group: 1.0 for group in range(groups)}
        member_idx = {
            group: np.array([col_pos[code] for code in members[group] if code in col_pos], dtype=np.intp)
            for group in range(groups)
        }
        for j in range(rebalance_index + 1, rebalance_index + 1 + horizon):
            if j >= len(dates):
                break
            for group in range(groups):
                idx = member_idx[group]
                if idx.size == 0:
                    continue
                if j == rebalance_index + 1:
                    px_in = open_np[j, idx]
                    with np.errstate(invalid="ignore", divide="ignore"):
                        ratio = np.clip(close_np[j, idx] / px_in - 1.0, -0.30, 0.30)
                    ret = float(np.nanmean(np.where(np.isfinite(ratio), ratio, np.nan)))
                    net = ret - turnover[group] * cost_bps / 10000.0
                else:
                    with np.errstate(invalid="ignore", divide="ignore"):
                        ratio = np.clip(close_np[j, idx] / close_np[j - 1, idx] - 1.0, -0.30, 0.30)
                    ret = float(np.nanmean(np.where(np.isfinite(ratio), ratio, np.nan)))
                    net = ret
                if not np.isfinite(ret):
                    ret, net = 0.0, 0.0
                period_gross[group] *= 1 + ret
                period_net[group] *= 1 + net
                level[group] *= 1 + net
                daily.loc[dates[j], f"Q{group + 1}"] = ret
                nav.loc[dates[j], f"Q{group + 1}"] = level[group]
            if j == rebalance_index + 1:
                ls_cost.loc[dates[j]] = (turnover[groups - 1] + turnover[0]) * cost_bps / 10000.0
        periods_net.append({f"Q{group + 1}": period_net[group] - 1 for group in range(groups)})
        periods_gross.append({f"Q{group + 1}": period_gross[group] - 1 for group in range(groups)})
        period_dates.append(dt)

    turnover_df = pd.DataFrame(turnover_rows, index=period_dates)
    return QuantileResult(
        nav=nav.ffill(),
        turnover_df=turnover_df,
        periods_net=pd.DataFrame(periods_net, index=period_dates),
        periods_gross=pd.DataFrame(periods_gross, index=period_dates),
        daily=daily,
        ls_cost=ls_cost,
        turnover_mean=turnover_df.mean(),
    )


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
