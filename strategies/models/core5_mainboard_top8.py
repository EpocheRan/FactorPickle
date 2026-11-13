"""Exact core5 signal, main-board-only, top 8."""

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


def _is_main_board(code: str) -> bool:
    """A-share main board: not STAR, not ChiNext, not BSE, not B-share."""
    code = str(code)
    if code.startswith(("300", "301", "688", "689", "8", "4", "920", "200", "900")):
        return False
    return code.startswith(("000", "001", "002", "003", "600", "601", "603", "605"))


MAIN_BOARD = tuple(sorted(code for code in POOL_2017_11_01_1000 if _is_main_board(code)))


class Core5MainBoardTop8(CrossSectionStrategy):
    name = "core5_mainboard_top8"
    universe = Universe.explicit(MAIN_BOARD)
    top_n = 8
    buffer = 0
    rebalance = Rebalance.MONTHLY
    weighting = EqualWeight()
    params = {"top_n": 8, "buffer": 0, "weighting": "equal"}

    def __init__(self, params=None):
        super().__init__(params)
        self.top_n = int(self.params["top_n"])
        self.buffer = int(self.params["buffer"])

    def factors(self) -> list[FactorDef]:
        return [F.custom("core5_signal", on="hfq_close", window=1, warmup=1), F.vol(20)]

    def rank(self, panel, dt: pd.Timestamp) -> pd.Series:
        return panel.at(dt, "core5_signal").dropna()


STRATEGY = Core5MainBoardTop8
