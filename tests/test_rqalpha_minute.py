"""RQAlpha 分钟模式：mock 1m 数据 + 日内止损（不写数据库）。"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from backtest.application.ports import EngineRequest
from backtest.domain.models import (
    DataBundle,
    ExecutionRules,
    RunSpec,
    TradingCalendar,
    Universe,
)
from backtest.infrastructure.data.snapshot import make_snapshot
from backtest.infrastructure.engine.rqalpha_engine import RQAlphaEngine
from strategies.base import SingleSymbolStrategy

DAYS = ("2024-01-02", "2024-01-03", "2024-01-04")


def _minutes(day: str) -> list[pd.Timestamp]:
    am = pd.date_range(f"{day} 09:31", f"{day} 11:30", freq="1min")
    pm = pd.date_range(f"{day} 13:01", f"{day} 15:00", freq="1min")
    return list(am) + list(pm)


def _frame() -> pd.DataFrame:
    rows = []
    for day in DAYS:
        for dt in _minutes(day):
            if day == "2024-01-02":
                price = 10.0
            elif day == "2024-01-03":
                price = 10.0 if dt.time() <= pd.Timestamp("10:00").time() else 9.5
            else:
                price = 9.5
            rows.append(
                {
                    "symbol": "600519",
                    "date": dt,
                    "open": price,
                    "high": price * 1.001,
                    "low": price * 0.999,
                    "close": price,
                    "volume": 10_000.0,
                    "amount": price * 10_000.0,
                    "adj_factor": 1.0,
                    "hfq_close": price,
                }
            )
    return pd.DataFrame(rows)


class MinuteStop(SingleSymbolStrategy):
    name = "minute_stop"
    symbols = ("600519",)

    def factors(self):
        return []

    def on_bar(self, ctx):
        if pd.Timestamp(ctx.dt) == pd.Timestamp("2024-01-02 10:00"):
            ctx.buy_shares(1000)
            ctx.set_stop(loss=9.8)


class DummyPanel:
    meta = {}

    def get(self, symbol, name, dt):
        return float("nan")


def _request(frame: pd.DataFrame) -> EngineRequest:
    calendar = TradingCalendar(tuple(pd.Timestamp(day) for day in DAYS))
    universe = Universe.from_codes(("600519",), as_of=date(2024, 1, 2), fingerprint="mock")
    data = DataBundle(
        frame=frame,
        calendar=calendar,
        snapshot=make_snapshot("mock", frame),
        universe=universe,
        security_status=None,
    )
    spec = RunSpec(
        strategy_name="minute_stop",
        strategy_path="",
        mode="single",
        symbols=("600519",),
        start=date(2024, 1, 2),
        end=date(2024, 1, 4),
        freq="1m",
        initial_cash=1_000_000,
        benchmark=None,
    )
    return EngineRequest(
        spec=spec,
        data=data,
        panel=DummyPanel(),
        strategy=MinuteStop(),
        universe=universe,
        calendar=calendar,
        rules=ExecutionRules(),
        benchmark=None,
    )


def test_minute_intraday_stop_fills_next_bar_open():
    result = RQAlphaEngine().run(_request(_frame()))
    assert result.meta["bar_frequency"] == "1m"
    assert result.meta["orders_filled"] == 2
    assert len(result.trades) == 2

    orders = result.orders.sort_values("date").reset_index(drop=True)
    assert orders.loc[0, "type"] == "buy"
    assert pd.Timestamp(orders.loc[0, "date"]) == pd.Timestamp("2024-01-02 10:01")
    assert orders.loc[1, "type"] == "sell"
    assert pd.Timestamp(orders.loc[1, "date"]) == pd.Timestamp("2024-01-03 10:02")
    assert float(orders.loc[1, "fill_price"]) == pytest.approx(9.5)

    assert result.meta["ledger_reconciliation"] == {}
    assert result.metrics["final_equity"] < 1_000_000
