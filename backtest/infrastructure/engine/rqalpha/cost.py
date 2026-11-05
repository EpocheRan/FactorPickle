"""把 FinBridge 的 A 股费用模型接入 RQAlpha。"""

from __future__ import annotations

from rqalpha.const import SIDE
from rqalpha.environment import Environment
from rqalpha.interface import (
    AbstractTransactionCostDecider,
    TransactionCost,
    TransactionCostArgs,
)

from backtest.infrastructure.execution.a_share import FeeModel


class FinBridgeCostDecider(AbstractTransactionCostDecider):
    def __init__(self, rules):
        self.rules = rules
        self.fee_model = FeeModel(rules)

    def calc(self, args: TransactionCostArgs) -> TransactionCost:
        value = float(args.price) * abs(int(args.quantity))
        if value <= 0:
            return TransactionCost.zero()
        trading_dt = Environment.get_instance().trading_dt
        commission = max(
            value * float(self.rules.commission_rate),
            float(self.rules.min_commission),
        )
        tax = (
            value * float(self.fee_model.stamp_rate(trading_dt))
            if args.side == SIDE.SELL
            else 0.0
        )
        other = value * float(self.rules.transfer_fee_rate)
        return TransactionCost(commission=commission, tax=tax, other_fees=other)
