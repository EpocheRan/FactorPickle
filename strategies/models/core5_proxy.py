"""Long-only proxy of the Alpha `core5` campaign composite.

This is a backtest-module strategy, not a promotion artifact.  The Alpha
`core5` DSL uses financial fields that are not all exposed as backtest factors;
this strategy uses the closest registered long-only proxies:

- revenue growth
- delta asset turnover
- gross margin (profitability proxy)
- Amihud-style absolute-return/amount (liquidity)
- log price (low-price leg)

The cross-sectional composite is equal-weight rank; monthly rebalance,
2017-11-01 liquidity-1000 pool.
"""

from __future__ import annotations

import pandas as pd

from factors import F, FactorDef
from strategies.base import CrossSectionStrategy
from strategies.private.pool_2017_1000 import POOL_2017_11_01_1000
from strategies.universe import Rebalance, Universe
from strategies.weighting import EqualWeight

PARTS = (
    ("liquidity.turnover_std_bias_21_504", -1, 1.0),
    ("momentum.rsrs", 1, 1.0),
    ("sentiment.main_net_buy_20d", 1, 1.0),
    ("risk.vol_63d", -1, 1.0),
    ("reversal.rsi_14d", -1, 1.0),
)


class Core5Proxy(CrossSectionStrategy):
    name = "core5_proxy"
    universe = Universe.explicit(POOL_2017_11_01_1000)
    top_n = 200
    buffer = 20
    rebalance = Rebalance.MONTHLY
    weighting = EqualWeight()
    params = {"top_n": 200, "buffer": 20, "weighting": "equal"}

    def __init__(self, params=None):
        super().__init__(params)
        self.top_n = int(self.params["top_n"])
        self.buffer = int(self.params["buffer"])
        self.composite_name = "core5_proxy"

    def factors(self) -> list[FactorDef]:
        return [F.ts(name) for name, _direction, _weight in PARTS] + [
            F.composite(PARTS, name=self.composite_name),
            F.vol(20),
        ]

    def rank(self, panel, dt: pd.Timestamp) -> pd.Series:
        return panel.at(dt, self.composite_name).dropna()


STRATEGY = Core5Proxy
