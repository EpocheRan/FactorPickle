"""Compatibility shim: the optional Polars backend lives in factor_metrics."""

from __future__ import annotations

from factor_metrics.backends.polars import (  # noqa: F401
    PolarsUnavailable,
    quantile_forward_spread,
    quantile_nav,
    rank_ic_frame,
)

__all__ = [
    "PolarsUnavailable",
    "quantile_forward_spread",
    "quantile_nav",
    "rank_ic_frame",
]
