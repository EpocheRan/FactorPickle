"""Parity check between legacy research panel construction and Runner panel.

This utility is intentionally separate from production paths.  It lets us prove
that DataInterfaceGateway + build_factor_panel produces the same market fields
as research._resolve_panel for an explicit code list before removing legacy
fallbacks.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from factor_lab.runtime.data_interface_adapter import DataInterfaceGateway
from factor_lab.runtime.panel_builder import build_factor_panel
from factor_lab.core.store import write_json_atomic


def _max_abs_diff(left: pd.DataFrame | None, right: pd.DataFrame | None) -> float | None:
    if left is None or right is None:
        return None
    index = left.index.intersection(right.index)
    columns = left.columns.intersection(right.columns)
    if len(index) == 0 or len(columns) == 0:
        return None
    delta = (left.loc[index, columns] - right.loc[index, columns]).abs()
    return float(np.nanmax(delta.to_numpy())) if delta.notna().any().any() else None


def compare_price_panel(
    *,
    factor_id: str,
    codes: list[str],
    start: str,
    end: str,
    gateway: DataInterfaceGateway | None = None,
    warmup_days: int = 800,
) -> dict[str, Any]:
    gateway = gateway or DataInterfaceGateway()
    artifact = {"factor_id": factor_id, "category": "price", "data_contract": {"reference": False}}
    new = build_factor_panel(gateway, artifact, codes, start, end, warmup_days=warmup_days)

    from research.report.runner import _resolve_panel

    universe = "explicit:" + ",".join(codes)
    legacy_panel, _factor = _resolve_panel(
        factor_id,
        None,
        len(codes),
        start,
        end,
        extras=(),
        universe=universe,
        use_cache=True,
    )
    fields = ("close", "open", "high", "low", "volume", "amount", "turnover")
    diffs = {
        field: _max_abs_diff(
            getattr(new.panel, field, None),
            getattr(legacy_panel, field, None),
        )
        for field in fields
    }
    overlap = len(new.panel.close.index.intersection(legacy_panel.close.index))
    result = {
        "schema_version": "factor_lab.parity.v1",
        "factor_id": factor_id,
        "codes": codes,
        "start": start,
        "end": end,
        "new_data_version": new.panel.freshness.get("data_version"),
        "legacy_data_version": legacy_panel.freshness.get("data_version"),
        "new_rows": int(len(new.panel.close)),
        "legacy_rows": int(len(legacy_panel.close)),
        "overlap_dates": int(overlap),
        "max_abs_diff": diffs,
        "symbols_equal": set(new.panel.symbols) == set(legacy_panel.symbols),
        "ok": bool(
            overlap > 0
            and set(new.panel.symbols) == set(legacy_panel.symbols)
            and all(value is None or value < 1e-3 for value in diffs.values())
        ),
    }
    return result


def write_parity(result: dict[str, Any], path: str | Path) -> Path:
    target = Path(path)
    write_json_atomic(target, result)
    return target
