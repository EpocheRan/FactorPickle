"""波段突破 + 换手波动偏离双因子横截面策略。

- ``risk.band_break_21d``：21 日区间突破强度，族先验方向 -1；
- ``liquidity.turnover_std_bias_21_504``：短期换手波动相对长期放大，
  拥挤度代理，族先验方向 -1；
- 两因子先转横截面百分位，再按 ``band_weight`` 合成；
- 显式 2017-11-01 成交额前 200 池，月度调仓，top_n + buffer；
- 不做行业/市值中性化，不做样本内权重优化。

回测::

    python -m backtest run strategies/models/band_turnover_combo.py \
        --start 2018-01-01 --end 2026-11-01
"""

from __future__ import annotations

import pandas as pd

from factors import F
from factors.direction import direction_of
from strategies.base import CrossSectionStrategy
from strategies.pools.pool_2017_200 import POOL_2017_11_01_200
from strategies.universe import Rebalance, Universe
from strategies.weighting import EqualWeight

BAND = "risk.band_break_21d"
TURNOVER = "liquidity.turnover_std_bias_21_504"


class BandTurnoverCombo(CrossSectionStrategy):
    name = "band_turnover_combo"
    universe = Universe.explicit(POOL_2017_11_01_200)
    top_n = 20
    buffer = 5
    rebalance = Rebalance.MONTHLY
    weighting = EqualWeight()
    params = {
        "top_n": 20,
        "buffer": 5,
        "band_weight": 0.5,
        "weighting": "equal",
    }

    def __init__(self, params=None):
        super().__init__(params)
        self.top_n = int(self.params["top_n"])
        self.buffer = int(self.params["buffer"])
        self.band_weight = float(self.params["band_weight"])
        if not 0.0 <= self.band_weight <= 1.0:
            raise ValueError("band_weight 必须在 [0, 1]")

    def factors(self):
        return [F.ts(BAND), F.ts(TURNOVER), F.vol(20)]

    def rank(self, panel, dt: pd.Timestamp) -> pd.Series:
        band = panel.at(dt, BAND) * direction_of(BAND)
        turnover = panel.at(dt, TURNOVER) * direction_of(TURNOVER)
        frame = pd.concat(
            [band.rename("band"), turnover.rename("turnover")], axis=1
        ).dropna()
        if frame.empty:
            return pd.Series(dtype="float64")
        ranks = frame.rank(pct=True)
        score = (
            self.band_weight * ranks["band"]
            + (1.0 - self.band_weight) * ranks["turnover"]
        )
        return score.dropna()


STRATEGY = BandTurnoverCombo
