"""zzshare 行情源适配器的离线单测（不联网）。"""

from __future__ import annotations

import pandas as pd
import pytest

from data_interface.market.sources.zzshare_source import ZzshareMarketSource


class FakeClient:
    def __init__(self, minute=None, daily=None):
        self._minute = minute if minute is not None else pd.DataFrame()
        self._daily = daily if daily is not None else pd.DataFrame()
        self.calls: list[tuple[str, dict]] = []

    def stk_mins(self, **kwargs):
        self.calls.append(("stk_mins", kwargs))
        return self._minute

    def daily(self, **kwargs):
        self.calls.append(("daily", kwargs))
        return self._daily


def _config():
    return {"zzshare": {"token": "x", "requests_per_minute": {"stk_mins": 6000, "daily": 6000}}}


def test_minute_parses_sorts_and_units():
    raw = pd.DataFrame(
        {
            "ts_code": ["600519.SH", "600519.SH", "600519.SH"],
            "trade_time": ["202611061500", "202611060935", "202611060940"],
            "open": [1450.02, 1450.0, 1450.98],
            "high": [1453.0, 1455.0, 1457.72],
            "low": [1448.98, 1436.44, 1450.97],
            "close": [1453.0, 1450.32, 1451.92],
            "vol": [367000.0, 749927.0, 227067.0],
            "amount": [533059569.0, 1085492000.0, 330347000.0],
        }
    )
    source = ZzshareMarketSource(_config(), client=FakeClient(minute=raw))
    frame = source.fetch_ohlc("600519", "2026-11-06", "2026-11-06", "5min")

    assert list(frame.columns)[:6] == ["open", "high", "low", "close", "volume", "amount"]
    assert frame["code"].iloc[0] == "600519"
    assert frame["market"].iloc[0] == "SSE"
    assert frame["type"].iloc[0] == "stock"
    assert len(frame) == 3
    assert list(frame.index) == sorted(frame.index)  # 接口倒序 -> 升序
    assert frame.index[0] == pd.Timestamp("2026-11-06 09:35")
    # vol 已经是「股」，不再乘 100
    assert frame["volume"].iloc[0] == 749927.0


def test_minute_trims_to_requested_window():
    raw = pd.DataFrame(
        {
            "ts_code": ["600519.SH", "600519.SH"],
            "trade_time": ["202611050935", "202611060935"],
            "open": [1.0, 2.0],
            "high": [1.0, 2.0],
            "low": [1.0, 2.0],
            "close": [1.0, 2.0],
            "vol": [10.0, 20.0],
            "amount": [1.0, 2.0],
        }
    )
    source = ZzshareMarketSource(_config(), client=FakeClient(minute=raw))
    frame = source.fetch_ohlc("600519", "2026-11-06", "2026-11-06", "5min")
    assert len(frame) == 1
    assert frame.index[0] == pd.Timestamp("2026-11-06 09:35")


def test_daily_parses_and_normalizes():
    raw = pd.DataFrame(
        {
            "ts_code": ["600519.SH"],
            "trade_date": ["20261106"],
            "open": [1450.0],
            "high": [1457.72],
            "low": [1436.0],
            "close": [1453.0],
            "pre_close": [1452.0],
            "change": [1.0],
            "pct_chg": [0.068871],
            "vol": [4284772.0],
            "amount": [6199528289.3],
        }
    )
    source = ZzshareMarketSource(_config(), client=FakeClient(daily=raw))
    frame = source.fetch_ohlc("600519", "2026-11-06", "2026-11-06", "d")
    assert frame.index[0] == pd.Timestamp("2026-11-06")
    assert frame["volume"].iloc[0] == 4284772.0
    assert frame["amount"].iloc[0] == pytest.approx(6199528289.3)


def test_unsupported_frequency_raises():
    source = ZzshareMarketSource(_config(), client=FakeClient())
    assert ZzshareMarketSource.available("5min") is True
    assert ZzshareMarketSource.available("d") is True
    assert ZzshareMarketSource.available("w") is False
    with pytest.raises(Exception):
        source.fetch_ohlc("600519", "2026-11-06", "2026-11-06", "w")


def test_missing_token_raises_when_client_needed():
    source = ZzshareMarketSource({"zzshare": {"token": ""}})
    with pytest.raises(Exception):
        source.fetch_ohlc("600519", "2026-11-06", "2026-11-06", "5min")
