"""Build a research ``Panel`` from DataInterfaceGateway bundles.

There is one Runner data path: DataInterfaceGateway -> DataBundle ->
build_factor_panel -> Panel.  The legacy research loader is not imported here.

Data rules:

- market bundle is required.
- every optional date x symbol matrix the gateway exposes is loaded:
  flow/moneyflow, margin, valuation, industry, regime.
- category metadata is descriptive only; it never gates data availability.
- all loaded extras are exposed as columns on ``Panel.frame()`` so generated
  factors can consume arbitrary data fields, not just OHLCV.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import pandas as pd

from data_interface.panel import Panel

from factor_lab.runtime.data_interface_adapter import DataBundle, DataGap, DataInterfaceGateway, DataManifest


class PanelBuildError(RuntimeError):
    pass


@dataclass
class BuiltPanel:
    panel: Panel
    manifest: DataManifest
    fetch_start: str
    eval_start: str
    category: str = ""


def _artifact_value(artifact: Any, key: str, default: Any = None) -> Any:
    if isinstance(artifact, dict):
        return artifact.get(key, default)
    return getattr(artifact, key, default)


def _artifact_spec(artifact: Any) -> dict[str, Any]:
    if isinstance(artifact, dict):
        spec = artifact.get("spec")
        return spec if isinstance(spec, dict) else artifact
    spec = getattr(artifact, "spec", None)
    return spec if isinstance(spec, dict) else {}


def factor_category(factor_artifact: Any) -> str:
    spec = _artifact_spec(factor_artifact)
    category = _artifact_value(factor_artifact, "category") or spec.get("category")
    if category:
        return str(category)
    factor_id = str(_artifact_value(factor_artifact, "factor_id", ""))
    return factor_id.split(".", 1)[0] if "." in factor_id else "price"


def factor_data_contract(factor_artifact: Any) -> dict[str, Any]:
    spec = _artifact_spec(factor_artifact)
    contract = _artifact_value(factor_artifact, "data_contract") or spec.get("data_contract")
    return dict(contract) if isinstance(contract, dict) else {}


def _plain_index(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    if "ts" in result.columns:
        result = result.set_index("ts")
    result.index = pd.to_datetime(result.index, errors="coerce").tz_localize(None).normalize()
    result = result[~result.index.isna()]
    result = result[~result.index.duplicated(keep="last")].sort_index()
    result.columns = [str(column).lower() for column in result.columns]
    return result


def _wide(bundle: DataBundle, field: str) -> pd.DataFrame | None:
    series: dict[str, pd.Series] = {}
    for code, raw in bundle.frames.items():
        frame = _plain_index(raw)
        if field not in frame.columns:
            continue
        values = pd.to_numeric(frame[field], errors="coerce")
        if values.notna().any():
            series[str(code)] = values
    if not series:
        return None
    return pd.DataFrame(series).sort_index()


def _pivot_extras(frames: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    result: dict[str, pd.DataFrame] = {}
    for name, frame in frames.items():
        if isinstance(frame, pd.DataFrame):
            normalized = frame.copy()
            if not isinstance(normalized.index, pd.DatetimeIndex):
                normalized = _plain_index(normalized)
            result[name] = normalized
    return result


def _safe_bundle(gateway: DataInterfaceGateway, method: str, *args: Any, **kwargs: Any) -> DataBundle | None:
    try:
        fn = getattr(gateway, method)
        return fn(*args, **kwargs)
    except Exception:  # noqa: BLE001 - reference data is optional
        return None


def build_factor_panel(
    gateway: DataInterfaceGateway,
    factor_artifact: Any,
    codes: list[str],
    start: str,
    end: str,
    *,
    warmup_days: int = 800,
    adjust: str = "qfq",
) -> BuiltPanel:
    if not codes:
        raise PanelBuildError("codes must not be empty")
    category = factor_category(factor_artifact)
    contract = factor_data_contract(factor_artifact)
    fetch_start = (pd.Timestamp(start) - timedelta(days=int(warmup_days))).date().isoformat()
    market = gateway.market_bundle(codes, start, end, warmup_days=warmup_days, adjust=adjust)
    close = _wide(market, "close")
    if close is None or close.empty:
        raise PanelBuildError("data_interface market bundle has no close field")
    open_ = _wide(market, "open")
    high = _wide(market, "high")
    low = _wide(market, "low")
    volume = _wide(market, "volume")
    amount = _wide(market, "amount")
    turnover = _wide(market, "turnover")
    columns = list(close.columns)

    def align(frame: pd.DataFrame | None) -> pd.DataFrame | None:
        if frame is None:
            return None
        return frame.reindex(index=close.index, columns=columns)

    extras: dict[str, pd.DataFrame] = {}
    manifests = [market.manifest]
    reference = bool(contract.get("reference", True))
    # Data availability is not a factor-category decision.  Fetch every
    # optional matrix the gateway exposes; a missing service only removes its
    # columns and is recorded in the manifest, never blocks an unrelated factor.
    for method in ("flow_bundle", "margin_bundle", "valuation_bundle", "financial_bundle", "event_bundle"):
        bundle = _safe_bundle(gateway, method, codes, fetch_start, end)
        if bundle is not None:
            extras.update(_pivot_extras(bundle.frames))
            manifests.append(bundle.manifest)

    # Optional matrices are aligned to the market panel.  Financial statement
    # fields are reported on sparse announcement dates; carry the latest
    # published value forward so they are usable as daily factor fields (the
    # announcement date itself is already PIT-aligned by data_interface).
    aligned_extras: dict[str, pd.DataFrame] = {}
    for name, frame in extras.items():
        aligned = align(frame)
        if aligned is None:
            continue
        if str(name).lower().startswith("fin_"):
            aligned = aligned.ffill()
        aligned_extras[name] = aligned
    extras = aligned_extras

    benchmark = None
    returns = close.pct_change(fill_method=None).mean(axis=1)
    if returns.notna().any():
        benchmark = (1.0 + returns.fillna(0.0)).cumprod()
        benchmark.name = "data_interface_equal_weight"

    market_cap = extras.get("float_mv")
    if market_cap is not None and market_cap.notna().any().any():
        median = float(market_cap.stack().median())
        if median < 1e6:
            market_cap = market_cap * 10000.0
    else:
        market_cap = None
    industry = None
    regime = None
    if reference:
        try:
            industry = gateway.industry_map()
        except Exception:  # noqa: BLE001
            industry = None
        try:
            regime = gateway.regime_series(close.index)
        except Exception:  # noqa: BLE001
            regime = None

    manifest = DataManifest(
        services=sorted({service for item in manifests for service in item.services}),
        fields=sorted({field for item in manifests for field in item.fields}),
        codes=columns,
        start=str(start),
        end=str(end),
        rows=int(len(close)),
        data_hash="-".join(item.data_hash for item in manifests),
    )
    manifest.source = "DataInterfaceGateway"
    panel = Panel(
        close=close,
        open=align(open_),
        market_cap=market_cap.reindex(index=close.index, columns=columns) if market_cap is not None else None,
        industry=industry,
        regime=regime,
        symbols=columns,
        high=align(high),
        low=align(low),
        volume=align(volume),
        amount=align(amount),
        turnover=align(turnover),
        extras=extras or None,
        benchmark=benchmark,
        pool={
            "selection": "data_interface",
            "symbols": len(columns),
            "fetch_start": fetch_start,
            "eval_start": str(pd.Timestamp(start).date()),
            "category": category,
        },
        freshness={
            "data_version": manifest.data_hash,
            "source": "DataInterfaceGateway",
            "fetch_start": fetch_start,
            "eval_start": str(pd.Timestamp(start).date()),
            "category": category,
        },
        eval_start=pd.Timestamp(start),
    )
    return BuiltPanel(
        panel=panel,
        manifest=manifest,
        fetch_start=fetch_start,
        eval_start=str(pd.Timestamp(start).date()),
        category=category,
    )
