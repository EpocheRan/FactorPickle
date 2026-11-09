"""Backend selection for report metrics.

The public ``metrics.build_report`` API and ``FactorReport`` schema stay
unchanged.  Quantile NAV is the only metric currently routed to Polars:

- ``pandas``: production-safe override
- ``polars``: forced Polars, for benchmark/debugging
- ``auto`` (default): Polars only for moderate cross-sections where the real
  benchmark showed a win; full-market panels stay on pandas

Rank IC deliberately stays on pandas: the full-sample benchmark showed only
~1.1x for Polars, which does not justify the conversion cost.
"""

from __future__ import annotations

import os
from typing import Literal

Backend = Literal["pandas", "polars", "auto"]

# Real benchmark: 200 symbols was ~1.36x faster with the Polars quantile NAV,
# 5773 symbols was ~0.94x (conversion/rank overhead dominates).  Keep auto
# conservative until the full membership path is made cheaper.
QUANTILE_POLARS_MAX_CELLS = 2_000_000


def requested_backend() -> str:
    return os.environ.get("REPORT_METRICS_BACKEND", "auto").strip().lower()


def polars_available() -> bool:
    try:
        import polars  # noqa: F401
    except Exception:  # noqa: BLE001
        return False
    return True


def resolve_quantile_backend(cells: int, requested: str | None = None) -> str:
    mode = (requested or requested_backend()).lower()
    if mode == "pandas":
        return "pandas"
    if mode == "polars":
        return "polars" if polars_available() else "pandas"
    if mode != "auto":
        raise ValueError(f"unknown REPORT_METRICS_BACKEND={mode!r}")
    if not polars_available():
        return "pandas"
    return "polars" if int(cells) <= QUANTILE_POLARS_MAX_CELLS else "pandas"


def engine_metadata(factor_shape: tuple[int, int]) -> dict[str, str]:
    cells = int(factor_shape[0]) * int(factor_shape[1])
    return {
        "rank_ic": "pandas",
        "quantile": resolve_quantile_backend(cells),
    }
