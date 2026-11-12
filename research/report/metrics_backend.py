"""Compatibility shim: report metrics backend selection lives in factor_metrics."""

from __future__ import annotations

from factor_metrics.backend import (  # noqa: F401
    QUANTILE_POLARS_MAX_CELLS,
    Backend,
    engine_metadata,
    polars_available,
    requested_backend,
    resolve_quantile_backend,
)

__all__ = [
    "QUANTILE_POLARS_MAX_CELLS",
    "Backend",
    "engine_metadata",
    "polars_available",
    "requested_backend",
    "resolve_quantile_backend",
]
