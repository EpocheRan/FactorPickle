"""Alpha panel valuation fields: deterministic de-duplication and derived extras.

``fin_valuation_daily`` is keyed by ``(ts, code, source)``, so one ``(ts, code)``
can hold two producers.  The gateway must resolve that tie deterministically and
must expose the derived ``float_share`` / ``list_days`` fields in the manifest.
"""

from __future__ import annotations

import pandas as pd
import pytest

from factor_lab.runtime.data_interface_adapter import (
    VALUATION_SOURCE_PRIORITY,
    DataInterfaceGateway,
)


def _valuation_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            # 2021-06-01 has two producers; priority must pick tushare_daily_basic
            {
                "ts": "2021-06-01", "code": "000001", "source": "tushare.daily_basic",
                "close": 13.0, "market_cap": 1.0e10, "float_market_cap": 9.0e9,
                "ev_sales": 1.0, "p_nwc": 2.0, "earnings_yield": 0.5,
            },
            {
                "ts": "2021-06-01", "code": "000001", "source": "tushare_daily_basic",
                "close": 13.0, "market_cap": 2.0e10, "float_market_cap": 1.0e10,
                "ev_sales": 3.0, "p_nwc": 4.0, "earnings_yield": 0.25,
            },
            # 2021-06-02 has only one producer
            {
                "ts": "2021-06-02", "code": "000001", "source": "tushare.daily_basic",
                "close": 13.5, "market_cap": 1.2e10, "float_market_cap": 1.1e10,
                "ev_sales": 5.0, "p_nwc": 6.0, "earnings_yield": 0.2,
            },
        ]
    )


def _gateway(frame: pd.DataFrame) -> DataInterfaceGateway:
    gateway = DataInterfaceGateway(client_factory=lambda: object())
    gateway.query_frame = lambda sql, params=None: frame.copy()  # type: ignore[assignment]
    return gateway


def test_valuation_bundle_dedupes_by_source_priority():
    assert VALUATION_SOURCE_PRIORITY["tushare_daily_basic"] > VALUATION_SOURCE_PRIORITY["akshare_indicator"]
    bundle = _gateway(_valuation_frame()).valuation_bundle(["000001"], "2021-06-01", "2021-06-02")
    assert bundle.kind == "valuation"
    assert list(bundle.frames["ev_sales"].index) == [
        pd.Timestamp("2021-06-01"),
        pd.Timestamp("2021-06-02"),
    ]
    # rows=3 in the table, 2 after the (ts, code) de-duplication
    assert bundle.manifest.rows == 2
    assert bundle.frames["ev_sales"].loc["2021-06-01", "000001"] == pytest.approx(3.0)
    assert bundle.frames["p_nwc"].loc["2021-06-01", "000001"] == pytest.approx(4.0)
    assert bundle.frames["earnings_yield"].loc["2021-06-01", "000001"] == pytest.approx(0.25)
    # single-source rows are untouched
    assert bundle.frames["ev_sales"].loc["2021-06-02", "000001"] == pytest.approx(5.0)


def test_valuation_bundle_aliases_and_float_share():
    bundle = _gateway(_valuation_frame()).valuation_bundle(["000001"], "2021-06-01", "2021-06-02")
    assert "total_mv" in bundle.frames and "market_cap" not in bundle.frames
    assert "float_mv" in bundle.frames and "float_market_cap" not in bundle.frames
    # float_share = float_market_cap / close, derived for the winning row
    assert bundle.frames["float_share"].loc["2021-06-01", "000001"] == pytest.approx(1.0e10 / 13.0)
    assert "float_share" in bundle.manifest.fields
    assert "total_mv" in bundle.manifest.fields


def test_valuation_bundle_raises_data_gap_on_empty_frame():
    from factor_lab.runtime.data_interface_adapter import DataGap

    with pytest.raises(DataGap):
        _gateway(pd.DataFrame()).valuation_bundle(["000001"], "2021-06-01", "2021-06-02")


def test_derived_extras_are_declared_in_the_manifest():
    """``list_days`` / ``float_share`` are added after the bundles are collected."""
    from factor_lab.runtime.panel_builder import build_factor_panel

    dates = pd.date_range("2021-06-01", periods=3, freq="D")
    close = pd.DataFrame({"000001": [13.0, 13.5, 14.0]}, index=dates)
    market = pd.DataFrame({"close": close["000001"]})

    class _Market:
        def ohlc(self, codes, start, end, **kwargs):
            frame = market.copy()
            frame.loc[:, "open"] = frame["close"]
            frame.loc[:, "high"] = frame["close"]
            frame.loc[:, "low"] = frame["close"]
            frame.loc[:, "volume"] = 100.0
            frame.loc[:, "amount"] = 1000.0
            return {str(code): frame.copy() for code in (codes if isinstance(codes, list) else [codes])}

    gateway = DataInterfaceGateway(market_loader_factory=lambda: _Market(), client_factory=lambda: object())
    gateway.flow_bundle = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("skip"))  # type: ignore[assignment]
    gateway.margin_bundle = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("skip"))  # type: ignore[assignment]
    gateway.financial_bundle = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("skip"))  # type: ignore[assignment]
    gateway.event_bundle = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("skip"))  # type: ignore[assignment]
    gateway.index_weight_bundle = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("skip"))  # type: ignore[assignment]
    gateway.valuation_bundle = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("skip"))  # type: ignore[assignment]
    gateway.security_master = lambda codes=None: pd.DataFrame(  # type: ignore[assignment]
        {"list_date": [pd.Timestamp("2015-04-01")]}, index=pd.Index(["000001"], name="code")
    )

    built = build_factor_panel(
        gateway,
        {"factor_id": "alpha.panel", "category": "price", "data_contract": {"reference": False}},
        ["000001"],
        "2021-06-01",
        "2021-06-03",
        warmup_days=0,
    )
    assert "list_days" in built.manifest.fields
    assert "list_days" in built.manifest.per_field_coverage
    assert built.manifest.per_field_coverage["list_days"] > 0.0
