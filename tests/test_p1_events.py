"""P1 event features: PIT lag, zero-fill, holder state and gateway wiring."""

from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
import pytest

import data_interface.p1_events as p1
from factor_lab.alpha.data_axes import P1_AXES, P0_AXES, catalog
from factor_lab.runtime.data_interface_adapter import DataInterfaceGateway


DATES = pd.DatetimeIndex(pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"]))


class _FakeDB:
    def query_dataframe(self, sql: str):
        if "block_trade" in sql:
            return pd.DataFrame(
                [
                    {"ts": "2024-01-02", "code": "000001", "price": 10.0, "vol": 100.0, "amount": 1000.0},
                    {"ts": "2024-01-03", "code": "000001", "price": 10.0, "vol": 200.0, "amount": 2000.0},
                ]
            )
        if "top_list" in sql:
            return pd.DataFrame(
                [{"ts": "2024-01-02", "code": "000001", "amount": 5000.0, "l_buy": 800.0, "l_sell": 300.0, "net_amount": 500.0}]
            )
        if "repurchase" in sql:
            return pd.DataFrame([{"ts": "2024-01-03", "code": "000001", "vol": 30.0, "amount": 300.0}])
        if "stk_holdernumber" in sql:
            return pd.DataFrame(
                [
                    {"ts": "2024-01-02", "code": "000001", "holder_num": 100.0},
                    {"ts": "2024-01-04", "code": "000001", "holder_num": 120.0},
                ]
            )
        raise AssertionError(sql)


def test_p1_flow_features_are_zero_filled_and_lagged(monkeypatch) -> None:
    monkeypatch.setattr(p1, "_trading_days", lambda start, end: DATES)
    bundle = p1.load_p1_events(["000001"], "2024-01-02", "2024-01-05", db=_FakeDB())
    block = bundle.frames["block_amount"]
    assert block.loc["2024-01-02", "000001"] == 0.0
    assert block.loc["2024-01-03", "000001"] == 1000.0
    assert block.loc["2024-01-04", "000001"] == 2000.0
    assert bundle.frames["top_net_amount"].loc["2024-01-03", "000001"] == 500.0
    assert bundle.frames["rep_amount"].loc["2024-01-04", "000001"] == 300.0
    assert bundle.frames["block_amount"].shape == (4, 1)


def test_holder_number_state_is_forward_filled_and_lagged(monkeypatch) -> None:
    monkeypatch.setattr(p1, "_trading_days", lambda start, end: DATES)
    bundle = p1.load_p1_events(["000001"], "2024-01-02", "2024-01-05", db=_FakeDB())
    holder = bundle.frames["holder_num"]["000001"]
    assert holder.loc["2024-01-02"] != holder.loc["2024-01-02"]  # NaN before first disclosure is usable
    assert holder.loc["2024-01-03"] == 100.0
    assert holder.loc["2024-01-04"] == 100.0
    assert holder.loc["2024-01-05"] == 120.0
    assert bundle.frames["holder_days_since"]["000001"].loc["2024-01-05"] == 1.0


def test_p1_catalog_and_gateway_event_bundle(monkeypatch) -> None:
    catalog_payload = catalog()
    assert catalog_payload["schema"] == "factor_lab.alpha.data_axes.v1"
    assert "margin_rzye" in P0_AXES["margin"]["fields"]
    assert set(P1_AXES["block_trade"]["fields"]) == set(p1.P1_EVENT_FIELDS["block_trade"])
    assert "block_amount" in catalog_payload["fields"]["p1"]

    frames = {"block_amount": pd.DataFrame([[1.0]], index=DATES[:1], columns=["000001"])}
    fake = SimpleNamespace(frames=frames, raw_rows={"block_trade": 1})
    monkeypatch.setattr(p1, "load_p1_events", lambda *args, **kwargs: fake)
    gateway = DataInterfaceGateway(client_factory=lambda: object())
    bundle = gateway.event_bundle(["000001"], "2024-01-02", "2024-01-02")
    assert bundle.kind == "p1_events"
    assert bundle.fields == ["block_amount"]
    assert bundle.manifest.services == ["p1_events"]
