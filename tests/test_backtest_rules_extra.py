"""A 股执行规则的边界用例（补充既有 test_backtest_execution.py）。"""

from __future__ import annotations

import pandas as pd
import pytest

from backtest.domain.models import ExecutionRules
from backtest.infrastructure.execution.a_share import (
    FeeModel,
    TPlusOneLedger,
    can_buy,
    can_sell,
    limit_prices,
    round_lot,
)


def _row(close: float, volume: float = 1000.0) -> dict:
    return {"close": close, "volume": volume}


def test_limit_price_rounding_to_cent():
    lower, upper = limit_prices(9.87, "600519")
    assert lower == pytest.approx(8.88)
    assert upper == pytest.approx(10.86)


def test_limit_prices_zero_prev_close_is_none():
    assert limit_prices(0.0, "600519") == (None, None)


def test_round_lot_custom_lot_size():
    assert round_lot(123, 1) == 123
    assert round_lot(123, 100) == 100
    assert round_lot(99, 100) == 0


def test_fee_min_commission_on_small_trade():
    model = FeeModel(ExecutionRules())
    fee = model.fee(shares=100, price=1.0, side="buy", dt="2024-01-02")
    assert fee == pytest.approx(5.0 + 0.001)


def test_fee_transfer_fee_component():
    model = FeeModel(ExecutionRules())
    fee = model.fee(shares=10_000, price=10.0, side="buy", dt="2024-01-02")
    assert fee == pytest.approx(25.0 + 1.0)


def test_fee_zero_value():
    model = FeeModel(ExecutionRules())
    assert model.fee(shares=0, price=10.0, side="buy", dt="2024-01-02") == 0.0


def test_t_plus_one_multi_symbol_independent():
    ledger = TPlusOneLedger(enabled=True)
    day1 = pd.Timestamp("2024-01-02").date()
    day2 = pd.Timestamp("2024-01-03").date()
    ledger.start_day(day1)
    ledger.on_buy("AAA", 100)
    ledger.on_buy("BBB", 200)
    assert ledger.available("AAA") == 0
    assert ledger.available("BBB") == 0
    ledger.start_day(day2)
    assert ledger.available("AAA") == 100
    assert ledger.available("BBB") == 200
    ledger.on_sell("AAA", 50)
    assert ledger.available("AAA") == 50
    assert ledger.available("BBB") == 200


def test_t_plus_one_start_day_is_idempotent():
    ledger = TPlusOneLedger(enabled=True)
    day1 = pd.Timestamp("2024-01-02").date()
    day2 = pd.Timestamp("2024-01-03").date()
    ledger.start_day(day1)
    ledger.on_buy("AAA", 100)
    ledger.start_day(day2)
    ledger.start_day(day2)
    assert ledger.available("AAA") == 100


def test_can_buy_at_limit_up_boundary():
    rules = ExecutionRules()
    ok, _ = can_buy(row=_row(109.99), prev_close=100.0, code="600519", rules=rules)
    assert ok
    blocked, reason = can_buy(row=_row(110.0), prev_close=100.0, code="600519", rules=rules)
    assert not blocked and reason == "limit_up"


def test_can_sell_suspended():
    rules = ExecutionRules()
    ok, reason = can_sell(row=_row(10.0, volume=0), prev_close=10.0, code="600519", rules=rules)
    assert not ok and reason == "suspended"
