"""Neutral date × symbol panel container shared by research and Factor Lab.

The dataclass used to live in ``research.report.panel``; it is data, not report
logic, so it now sits in the data layer.  Both the report loader and the Alpha
``panel_builder`` can import it without a ``factor_lab -> research`` dependency.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd


@dataclass
class Panel:
    close: pd.DataFrame
    open: pd.DataFrame
    market_cap: pd.DataFrame | None
    industry: pd.DataFrame | None
    regime: pd.Series | None
    symbols: list[str]
    regime_alt: pd.Series | None = None
    high: pd.DataFrame | None = None
    low: pd.DataFrame | None = None
    volume: pd.DataFrame | None = None
    amount: pd.DataFrame | None = None
    turnover: pd.DataFrame | None = None
    close_raw: pd.DataFrame | None = None
    extras: dict | None = None
    open_raw: pd.DataFrame | None = None
    benchmark: pd.Series | None = None
    stock_info: pd.DataFrame | None = None
    is_st: dict[str, bool] = field(default_factory=dict)
    pool: dict = field(default_factory=dict)
    freshness: dict = field(default_factory=dict)
    #: 评估起点；close/open 会多取一段预热数据，因子面板在它之前的部分要裁掉
    eval_start: pd.Timestamp | None = None
    _frame: pd.DataFrame | None = field(default=None, repr=False)

    @property
    def data_cutoff(self) -> pd.Timestamp:
        return self.close.index.max()

    def frame(self) -> pd.DataFrame:
        """长表（ts × symbol），供按标的计算因子；只构建一次。"""
        if self._frame is not None:
            return self._frame
        close = self.close
        n_rows, n_cols = close.shape
        index = np.repeat(close.index.to_numpy(), n_cols)
        symbol = np.tile(close.columns.to_numpy(), n_rows)

        def flat(panel: pd.DataFrame | None) -> np.ndarray:
            if panel is None:
                return np.full(n_rows * n_cols, np.nan)
            return panel.reindex(index=close.index, columns=close.columns).to_numpy().ravel()

        data = {
            "ts": index,
            "symbol": symbol,
            "close": flat(close),
            "hfq_close": flat(close),
            "open": flat(self.open),
            "high": flat(self.high),
            "low": flat(self.low),
            "hfq_high": flat(self.high),
            "hfq_low": flat(self.low),
            "volume": flat(self.volume),
            "amount": flat(self.amount),
            "turnover": flat(self.turnover),
            "float_mv": flat(self.market_cap),
        }
        # Any extra date x symbol matrix loaded by the panel builder is exposed
        # as a factor column.  This is the generic data path: no category gate.
        for name, extra in (self.extras or {}).items():
            key = str(name).lower()
            if key in data or not isinstance(extra, pd.DataFrame):
                continue
            data[key] = flat(extra)
        if self.benchmark is not None and not self.benchmark.empty:
            bench = self.benchmark.reindex(close.index).to_numpy(dtype="float64")
            data["benchmark_close"] = np.tile(bench, n_cols)
            data["benchmark_volume"] = np.ones(n_rows * n_cols)
        self._frame = pd.DataFrame(data)
        return self._frame
