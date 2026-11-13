"""core5, main-board top-1000 panel, top 8, 20% trailing stop."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from factors import F, FactorDef, factor
from strategies.base import CrossSectionStrategy
from strategies.universe import Rebalance, Universe
from strategies.weighting import EqualWeight

_CODES_PATH = Path(__file__).resolve().parents[2] / "output/factor_lab/alpha/data_axes/mainboard_liquid25_codes.json"
CODES = tuple(json.loads(_CODES_PATH.read_text(encoding="utf-8")))


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


class Core5Mainboard25Top8(CrossSectionStrategy):
    name = "core5_mainboard25_top8_30d"
    universe = Universe.explicit(CODES)
    top_n = 8
    buffer = 0
    rebalance = Rebalance.EVERY_N_BARS
    every_n_bars = 30
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



STRATEGY = Core5Mainboard25Top8
