"""Serialize FinBridge's in-memory ``FactorReport`` without changing FinBridge."""

from __future__ import annotations

from typing import Any

import pandas as pd

from .store import jsonable

SERIES_FIELDS = {
    "ic_by_year",
    "ic_by_month",
    "ic_by_regime",
    "ic_by_size",
    "ic_by_industry",
    "decay",
    "rolling_ic",
    "turnover_by_group",
}

FRAME_FIELDS = {
    "ic_by_year_ci",
    "ic_by_month_ci",
    "regime_stats",
    "regime_stats_alt",
    "regime_share",
    "period_returns_gross",
    "period_returns_net",
    "cost_table",
    "pool_robustness",
    "annual_ic_return",
    "ic_neutral",
}

SCALAR_FIELDS = {
    "factor_id",
    "category",
    "direction",
    "universe",
    "start",
    "end",
    "n_symbols",
    "n_dates",
    "coverage",
    "horizons",
    "monotonicity",
    "monotonicity_net",
    "monotonicity_p",
    "cost_mean",
    "turnover",
    "raw_direction",
    "flipped",
    "direction_source",
}

DICT_FIELDS = {
    "quantile_returns",
    "quantile_returns_gross",
    "quantile_ci",
    "spread_gross",
    "spread_net",
    "transmission",
    "attribution",
    "reproduction",
    "long_short",
    "perf",
    "tradeability",
    "capacity",
    "incrementality",
    "definition",
    "factor_stats",
    "data_quality",
    "pool",
    "freshness",
    "warnings",
    "sensitivity",
    "rating",
    "diagnostics",
    "decay_note",
    "winsor",
    "return_winsor",
    "tail_specificity",
}


def series_payload(value: Any) -> Any:
    if isinstance(value, pd.Series):
        return {
            "index": [jsonable(item) for item in value.index.tolist()],
            "values": [jsonable(item) for item in value.tolist()],
        }
    return jsonable(value)


def frame_payload(value: Any) -> Any:
    if isinstance(value, pd.DataFrame):
        return {
            "index": [jsonable(item) for item in value.index.tolist()],
            "columns": [jsonable(item) for item in value.columns.tolist()],
            "data": [[jsonable(item) for item in row] for row in value.to_numpy().tolist()],
        }
    return jsonable(value)


def ic_stats_payload(value: Any) -> Any:
    if isinstance(value, pd.DataFrame):
        rows = []
        for index, row in value.iterrows():
            item = {"horizon": jsonable(index)}
            item.update({str(column): jsonable(row[column]) for column in value.columns})
            rows.append(item)
        return rows
    return jsonable(value)


def report_to_dict(report: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name in SCALAR_FIELDS | DICT_FIELDS | SERIES_FIELDS | FRAME_FIELDS:
        if not hasattr(report, name):
            continue
        value = getattr(report, name)
        if value is None:
            continue
        if name in SERIES_FIELDS:
            out[name] = series_payload(value)
        elif name in FRAME_FIELDS:
            out[name] = frame_payload(value)
        elif name == "ic_stats":
            out[name] = ic_stats_payload(value)
        elif name == "incrementality" and isinstance(value, dict):
            # ``residual`` holds a full peer panel and can be many MB; the
            # summary fields are enough for audit/dashboard consumers.
            out[name] = {key: jsonable(item) for key, item in value.items() if key != "residual"}
        else:
            out[name] = jsonable(value)

    if hasattr(report, "ic"):
        out["ic"] = {
            str(horizon): series_payload(series)
            for horizon, series in dict(report.ic).items()
        }
    if hasattr(report, "ic_stats"):
        out["ic_stats"] = ic_stats_payload(report.ic_stats)
    return out
