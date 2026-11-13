"""C1/C9 Alpha composites on the main-board top-1000 panel, top 8."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from factors import F, FactorDef, factor
from strategies.base import CrossSectionStrategy
from strategies.universe import Rebalance, Universe
from strategies.weighting import EqualWeight

_CODES_PATH = Path(__file__).resolve().parents[2] / "output/factor_lab/alpha/data_axes/mainboard_1000_codes.json"
CODES = tuple(json.loads(_CODES_PATH.read_text(encoding="utf-8")))


@factor("c1_signal", unit="ratio", window=1, warmup=1, inputs=("c1_signal",), on="hfq_close")
def c1_signal(frame: pd.DataFrame) -> pd.Series:
    return pd.to_numeric(frame["c1_signal"], errors="coerce")


@factor("c9_signal", unit="ratio", window=1, warmup=1, inputs=("c9_signal",), on="hfq_close")
def c9_signal(frame: pd.DataFrame) -> pd.Series:
    return pd.to_numeric(frame["c9_signal"], errors="coerce")


class AlphaC1C9Top10Trailing20(CrossSectionStrategy):
    name = "alpha_c1_c9_top10_trailing20"
    universe = Universe.explicit(CODES)
    top_n = 8
    buffer = 0
    rebalance = Rebalance.EVERY_N_BARS
    every_n_bars = 20
    weighting = EqualWeight()
    params = {"signal": "c9_signal", "top_n": 10, "buffer": 0, "holding_days": 20, "trailing_pct": 0.20, "weighting": "equal"}

    def __init__(self, params=None):
        super().__init__(params)
        self.signal = str(self.params["signal"])
        self.top_n = int(self.params["top_n"])
        self.buffer = int(self.params["buffer"])
        self.every_n_bars = int(self.params["holding_days"])

    def factors(self) -> list[FactorDef]:
        return [F.custom(self.signal, on="hfq_close", window=1, warmup=1), F.vol(20)]

    def rank(self, panel, dt: pd.Timestamp) -> pd.Series:
        return panel.at(dt, self.signal).dropna()

    def on_bar(self, ctx) -> None:
        if ctx.pos > 0:
            ctx.set_stop(trailing_pct=float(self.params["trailing_pct"]))


STRATEGY = AlphaC1C9Top10Trailing20
