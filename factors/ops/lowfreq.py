"""低频数据因子：股东户数、解禁压力、回购进度。

数据来自季度/事件披露表，**必须按公告日（ann_date）对齐并前值填充**，
否则会用到未来信息。这三个算子只负责"给定单只股票的时间序列，算出因子值"，
数据对齐由 extras 的 asof 连接负责（见 research/eval/data.py）。

约定列名（由 extras 提供，日频、已 ffill）：
- ``holder_num``       股东户数（季度披露，ffill 到日频）
- ``holder_num_prev``  上一季度股东户数（用于算变化率）
- ``unlock_ratio_60d`` 未来 60 日解禁股数 / 当前流通股数
- ``repurchase_ratio`` 过去 120 日回购金额 / 流通市值
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..library import factor


def _col(frame: pd.DataFrame, name: str) -> pd.Series:
    if name in frame.columns:
        return pd.to_numeric(frame[name], errors="coerce").astype("float64")
    return pd.Series(np.nan, index=frame.index, dtype="float64")


@factor(
    "sentiment.holder_num_chg_q",
    unit="ratio",
    window=63,
    inputs=("holder_num", "holder_num_prev"),
)
def holder_num_chg_q(frame: pd.DataFrame) -> pd.Series:
    """股东户数季度变化率：户数减少 = 筹码集中，方向 -1。"""
    cur = _col(frame, "holder_num")
    prev = _col(frame, "holder_num_prev")
    return cur / prev.replace(0, np.nan) - 1.0


@factor(
    "event.unlock_pressure_60d",
    unit="ratio",
    window=60,
    inputs=("unlock_ratio_60d",),
)
def unlock_pressure_60d(frame: pd.DataFrame) -> pd.Series:
    """未来 60 日解禁股数占比：供给冲击，方向 -1。"""
    return _col(frame, "unlock_ratio_60d")


@factor(
    "event.repurchase_intensity_120d",
    unit="ratio",
    window=120,
    inputs=("repurchase_ratio",),
)
def repurchase_intensity_120d(frame: pd.DataFrame) -> pd.Series:
    """过去 120 日回购金额 / 流通市值：公司态度，方向 +1。"""
    return _col(frame, "repurchase_ratio")


LOWFREQ_FACTORS: dict[str, tuple] = {
    "sentiment.holder_num_chg_q": (holder_num_chg_q, 63, "ratio"),
    "event.unlock_pressure_60d": (unlock_pressure_60d, 60, "ratio"),
    "event.repurchase_intensity_120d": (repurchase_intensity_120d, 120, "ratio"),
}


def lookup(name: str) -> tuple:
    """返回 (fn, window, unit)；与 price/flow/delta 的 lookup 同构。"""
    return LOWFREQ_FACTORS[name]


__all__ = [
    "LOWFREQ_FACTORS",
    "holder_num_chg_q",
    "lookup",
    "repurchase_intensity_120d",
    "unlock_pressure_60d",
]
