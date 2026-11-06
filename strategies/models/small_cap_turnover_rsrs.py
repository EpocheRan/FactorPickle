"""小市值 + 换手波动偏离 + RSRS 三因子横截面策略。

- 小市值：``size.float_mv``，方向 +1（值越大 = 市值越小）；
- 换手波动偏离：默认 ``liquidity.turnover_std_bias_42_252``，方向 -1；
  可通过 ``--param turnover_factor=...`` 切回 21_504 做对照；
- RSRS：``momentum.rsrs``，方向 +1；
- 股票池：全 A（剔除 ST），在 rank 中显式剔除北交所 920/8/4 号段；
- 三因子先转横截面百分位，再按权重合成，月度调仓 top_n。

这是 baseline 融合，不做行业/市值中性化，也不做样本内权重优化。
"""

from __future__ import annotations

import pandas as pd

from factors import F
from strategies.base import CrossSectionStrategy
from strategies.universe import Rebalance, Universe, UniverseSpec
from strategies.weighting import EqualWeight, InverseVolWeight

TURNOVER_DEFAULT = "liquidity.turnover_std_bias_42_252"
RSRS = "momentum.rsrs"
SIZE = "size.float_mv"
AMOUNT = "liquidity.amount_ma_20d"


def _is_bse(symbol: str) -> bool:
    code = str(symbol)
    return code.startswith(("920", "8", "4"))


class SmallCapTurnoverRsrs(CrossSectionStrategy):
    name = "small_cap_turnover_rsrs"
    universe: UniverseSpec = Universe.all_a(exclude_st=True, min_bars=120)
    top_n = 20
    buffer = 5
    rebalance = Rebalance.MONTHLY
    weighting = EqualWeight()
    params = {
        "top_n": 20,
        "buffer": 5,
        "weighting": "equal",
        "size_weight": 1.0 / 3.0,
        "turn_weight": 1.0 / 3.0,
        "rsrs_weight": 1.0 / 3.0,
        "min_amount": 1e8,
        "exclude_bse": True,
        "turnover_factor": TURNOVER_DEFAULT,
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
        return [
            F.ts(str(self.params["turnover_factor"])),
            F.ts(RSRS),
            F.ts(SIZE),
            F.ts(AMOUNT),
            F.vol(20),
        ]

    def rank(self, panel, dt: pd.Timestamp) -> pd.Series:
        turnover = panel.at(dt, str(self.params["turnover_factor"])) * -1.0
        rsrs = panel.at(dt, RSRS)
        size = panel.at(dt, SIZE)
        amount = panel.at(dt, AMOUNT)
        frame = pd.concat(
            [
                turnover.rename("turnover"),
                rsrs.rename("rsrs"),
                size.rename("size"),
                amount.rename("amount"),
            ],
            axis=1,
        ).dropna()
        if bool(self.params.get("exclude_bse", True)):
            frame = frame[[not _is_bse(code) for code in frame.index]]
        min_amount = float(self.params.get("min_amount") or 0.0)
        if min_amount > 0:
            frame = frame[frame["amount"] >= min_amount]
        if frame.empty:
            return pd.Series(dtype="float64")
        ranks = frame[["turnover", "rsrs", "size"]].rank(pct=True)
        score = (
            float(self.params["size_weight"]) * ranks["size"]
            + float(self.params["turn_weight"]) * ranks["turnover"]
            + float(self.params["rsrs_weight"]) * ranks["rsrs"]
        )
        return score.dropna()


STRATEGY = SmallCapTurnoverRsrs
