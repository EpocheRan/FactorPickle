"""低换手 Top10：月末按过去 20 个交易日平均换手率排序，取最低的 10 只。

规则（与视频口径一致）：
1. 月末最后一个交易日，计算全市场过去 20 个交易日的平均换手率；
2. 由低到高排序，取最小的 10 只；
3. 下月第一个交易日**开盘**等权买入；
4. 当月最后一个交易日**收盘**全部卖出。

因子 `liquidity.turnover_ma_20d` 的族先验方向为 −1（换手越低越好），
所以这里直接 `score = -turnover_20d`，不做样本内翻转。
"""

from __future__ import annotations

from factors import F
from strategies.base import CrossSectionStrategy
from strategies.universe import Rebalance, Universe


class LowTurnoverTop10(CrossSectionStrategy):
    name = "low_turnover_top10"
    universe = Universe.all_a(exclude_st=True, min_bars=120)
    top_n = 10
    buffer = 0                 # 每月全换，不留缓冲
    rebalance = Rebalance.MONTHLY
    params = {
        "top_n": 10,
        "window": 20,
        "index": "",           # 留空 = 全 A；填 000905 等则限定指数成分
    }

    def __init__(self, params=None):
        super().__init__(params)
        self.top_n = int(self.params["top_n"])
        self.window = int(self.params["window"])
        index = str(self.params.get("index") or "").strip()
        if index:
            self.universe = Universe.index(index.zfill(6) if index.isdigit() else index)

    def factors(self):
        return [F.ts(f"liquidity.turnover_ma_{self.window}d")]

    def rank(self, panel, dt):
        raw = panel.at(dt, f"liquidity.turnover_ma_{self.window}d")
        return -raw          # 换手越小分越高


STRATEGY = LowTurnoverTop10
