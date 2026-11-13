"""Forward-return convention (copied verbatim from research.report.metrics).

T 日收盘出信号，T+1 开盘买入，T+h 收盘卖出：``close[t+h] / open[t+1] - 1``.
"""

from __future__ import annotations

import pandas as pd


def forward_returns(
    close: pd.DataFrame, open_: pd.DataFrame, horizons: tuple[int, ...]
) -> dict[int, pd.DataFrame]:
    """T+1 开盘买入、T+h 收盘卖出的持有收益。"""
    entry = open_.shift(-1)
    return {h: close.shift(-h) / entry - 1 for h in horizons}
