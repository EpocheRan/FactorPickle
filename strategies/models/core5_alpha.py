"""Exact core5 signal, executed through the backtest module.

The Alpha campaign computes `core5` with DSL financial fields.  This strategy
reads a precomputed `core5_signal` column supplied by a memory data source; the
backtest module remains responsible for portfolio construction, T+1 execution,
fees and accounting.
"""

from __future__ import annotations

import pandas as pd

from factors import F, FactorDef, factor
from strategies.base import CrossSectionStrategy
from strategies.private.pool_2017_1000 import POOL_2017_11_01_1000
from strategies.universe import Rebalance, Universe
from strategies.weighting import EqualWeight


@factor(
    "core5_signal",
    unit="ratio",
    window=1,
    warmup=1,
    inputs=("core5_signal",),
    on="hfq_close",
)
def core5_signal(frame: pd.DataFrame) -> pd.Series:
    return pd.to_numeric(frame["core5_signal"], errors="coerce")


class Core5Alpha(CrossSectionStrategy):
    name = "core5_alpha"
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

    def factors(self) -> list[FactorDef]:
        return [F.custom("core5_signal", on="hfq_close", window=1, warmup=1), F.vol(20)]

    def rank(self, panel, dt: pd.Timestamp) -> pd.Series:
        return panel.at(dt, "core5_signal").dropna()


STRATEGY = Core5Alpha
