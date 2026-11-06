"""换手波动偏离 + RSRS 双因子横截面策略。

组合逻辑
--------
- 换手波动偏离：``liquidity.turnover_std_bias_21_504``，方向 -1
  （短期换手波动相对长期放大 → 拥挤 → 未来收益低）；
- RSRS：``momentum.rsrs``，方向 +1（阻力支撑相对强度高 → 趋势强）；
- 每个因子先转横截面百分位，再按 ``turn_weight`` 等权/加权合成；
- 只在两个因子都有效时参与排名，避免单因子缺失被当成中性。

这是 baseline 组合，不做行业/市值中性化，也不做样本内权重优化。
"""

from __future__ import annotations

import pandas as pd

from factors import F
from strategies.base import CrossSectionStrategy
from strategies.pools.pool_2017_200 import POOL_2017_11_01_200
from strategies.universe import Rebalance, Universe, UniverseSpec
from strategies.weighting import EqualWeight, InverseVolWeight

TURNOVER = "liquidity.turnover_std_bias_21_504"
RSRS = "momentum.rsrs"


class TurnoverRsrsCombo(CrossSectionStrategy):
    name = "turnover_rsrs_combo"
    universe: UniverseSpec = Universe.explicit(POOL_2017_11_01_200)
    top_n = 20
    buffer = 5
    rebalance = Rebalance.MONTHLY
    weighting = EqualWeight()
    params = {
        "top_n": 20,
        "buffer": 5,
        "weighting": "equal",
        "turn_weight": 0.5,
    }

    def __init__(self, params=None):
        super().__init__(params)
        self.top_n = int(self.params["top_n"])
        self.buffer = int(self.params["buffer"])
        if str(self.params["weighting"]).lower() in ("inverse_vol", "inv_vol"):
            self.weighting = InverseVolWeight("vol20", cap=0.08)
        else:
            self.weighting = EqualWeight()

    def factors(self):
        return [F.ts(TURNOVER), F.ts(RSRS), F.vol(20)]

    def rank(self, panel, dt: pd.Timestamp) -> pd.Series:
        turnover = panel.at(dt, TURNOVER) * -1.0
        rsrs = panel.at(dt, RSRS)
        frame = pd.concat(
            [turnover.rename("turnover"), rsrs.rename("rsrs")], axis=1
        ).dropna()
        if frame.empty:
            return pd.Series(dtype="float64")
        ranks = frame.rank(pct=True)
        weight = float(self.params["turn_weight"])
        score = weight * ranks["turnover"] + (1.0 - weight) * ranks["rsrs"]
        return score.dropna()


STRATEGY = TurnoverRsrsCombo
