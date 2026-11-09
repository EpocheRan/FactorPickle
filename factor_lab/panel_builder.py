"""Build a research ``Panel`` from data_interface frames.

This is the bridge for a Runner-owned data path.  The legacy
``research.report.runner.generate`` path remains unchanged when ``panel=None``;
when a panel is supplied, ``generate`` skips ``_resolve_panel`` and computes the
factor from this object.

It intentionally supports price/volume panels first.  Financial/flow factors
need their own extras and should be added explicitly rather than silently
falling back to a different data source.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import pandas as pd

from research.report.panel import Panel

from .data_interface_adapter import DataInterfaceGateway, DataManifest


class PanelBuildError(RuntimeError):
    pass


@dataclass
class BuiltPanel:
    panel: Panel
    manifest: DataManifest
    fetch_start: str
    eval_start: str


def _plain_index(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    if "ts" in result.columns:
        result = result.set_index("ts")
    result.index = pd.to_datetime(result.index, errors="coerce").tz_localize(None).normalize()
    result = result[~result.index.isna()]
    result = result[~result.index.duplicated(keep="last")].sort_index()
    result.columns = [str(column).lower() for column in result.columns]
    return result


def _wide(frames: dict[str, pd.DataFrame], field: str) -> pd.DataFrame | None:
    series: dict[str, pd.Series] = {}
    for code, raw in frames.items():
        frame = _plain_index(raw)
        if field not in frame.columns:
            continue
        values = pd.to_numeric(frame[field], errors="coerce")
        if values.notna().any():
            series[str(code)] = values
    if not series:
        return None
    return pd.DataFrame(series).sort_index()


def build_price_panel(
    gateway: DataInterfaceGateway,
    codes: list[str],
    start: str,
    end: str,
    *,
    warmup_days: int = 800,
    adjust: str = "hfq",
) -> BuiltPanel:
    if not codes:
        raise PanelBuildError("codes must not be empty")
    fetch_start = (pd.Timestamp(start) - timedelta(days=int(warmup_days))).date().isoformat()
    frames, manifest = gateway.ohlc(codes, fetch_start, end, adjust=adjust)
    close = _wide(frames, "close")
    if close is None or close.empty:
        raise PanelBuildError("data_interface frames have no close field")
    open_ = _wide(frames, "open")
    high = _wide(frames, "high")
    low = _wide(frames, "low")
    volume = _wide(frames, "volume")
    amount = _wide(frames, "amount")
    turnover = _wide(frames, "turnover")

    columns = list(close.columns)
    def align(frame: pd.DataFrame | None) -> pd.DataFrame | None:
        if frame is None:
            return None
        return frame.reindex(index=close.index, columns=columns)

    benchmark = None
    returns = close.pct_change(fill_method=None).mean(axis=1)
    if returns.notna().any():
        benchmark = (1.0 + returns.fillna(0.0)).cumprod()
        benchmark.name = "data_interface_equal_weight"

    panel = Panel(
        close=close,
        open=align(open_),
        market_cap=None,
        industry=None,
        regime=None,
        symbols=columns,
        high=align(high),
        low=align(low),
        volume=align(volume),
        amount=align(amount),
        turnover=align(turnover),
        benchmark=benchmark,
        pool={
            "selection": "data_interface",
            "symbols": len(columns),
            "fetch_start": fetch_start,
            "eval_start": str(pd.Timestamp(start).date()),
        },
        freshness={
            "data_version": manifest.data_hash,
            "source": "data_interface",
            "fetch_start": fetch_start,
            "eval_start": str(pd.Timestamp(start).date()),
        },
        eval_start=pd.Timestamp(start),
    )
    return BuiltPanel(
        panel=panel,
        manifest=manifest,
        fetch_start=fetch_start,
        eval_start=str(pd.Timestamp(start).date()),
    )
