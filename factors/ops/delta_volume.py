"""成交量 Delta 强度因子（Pine「Smoothed Delta WMA」的日线化版本）。

原脚本用逐笔主动买卖量（``requestUpAndDownVolume``）计算 delta；
本实现用 CLV（收盘在当日区间的位置）乘成交量作为日线代理：

    clv_t      = ((c - l) - (h - c)) / (h - l)      # ∈ [-1, 1]
    delta_t    = clv_t * volume
    e_t        = EMA(delta, 7)
    s_t        = EMA(e_t, 14)
    sd_t       = s_t - s_t.shift(7)                 # adaptive_period = round(14/2)
    enh_t      = sd_t * (1 + ROC(s_t, 1) * 0.01)
    scale_t    = rolling_max(|sd|, 27) * 0.72
    strength_t = enh_t / scale_t
    factor     = sigmoid((strength_t + 1) * 0.75) * 100

方向 +1：强度越高，买压越强。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..library import factor

_EMA_FAST = 7
_EMA_SLOW = 14
_THRESHOLD_WINDOW = 27
_THRESHOLD_K = 0.72
_SENSITIVITY = 0.15


def _col(frame: pd.DataFrame, name: str) -> pd.Series:
    if name in frame.columns:
        return pd.to_numeric(frame[name], errors="coerce").astype("float64")
    return pd.Series(np.nan, index=frame.index, dtype="float64")


def _delta_series(frame: pd.DataFrame) -> pd.Series:
    """CLV × 成交量；区间为 0（一字板/停牌）时返回 NaN。"""
    high, low, close = _col(frame, "high"), _col(frame, "low"), _col(frame, "close")
    volume = _col(frame, "volume")
    span = (high - low).replace(0, np.nan)
    clv = ((close - low) - (high - close)) / span
    return (clv.fillna(0.0) * volume).where(volume.notna())


def _smoothed_delta(frame: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    delta = _delta_series(frame)
    ema_fast = delta.ewm(span=_EMA_FAST, adjust=False, min_periods=_EMA_FAST).mean()
    smooth = ema_fast.ewm(span=_EMA_SLOW, adjust=False, min_periods=_EMA_SLOW).mean()
    adaptive = max(round(_EMA_SLOW / 2), 5)
    return smooth - smooth.shift(adaptive), smooth


@factor(
    "volume.delta_strength_27d",
    unit="score",
    window=_THRESHOLD_WINDOW,
    inputs=("high", "low", "close", "volume"),
)
def delta_strength_27d(frame: pd.DataFrame) -> pd.Series:
    """动态阈值标准化后的 Delta 强度（0-100），等价于原脚本的 normalized_buy。"""
    smoothed_delta, smooth = _smoothed_delta(frame)
    momentum = smooth.pct_change(fill_method=None) * 0.01
    enhanced = smoothed_delta * (1.0 + momentum)

    scale = smoothed_delta.abs().rolling(_THRESHOLD_WINDOW, min_periods=10).max() * _THRESHOLD_K
    scale = scale.replace(0, np.nan)
    # buy_threshold = -scale，故 (enhanced - buy_threshold) = enhanced + scale
    scaled = (enhanced + scale) * _SENSITIVITY * 5
    return 100.0 * (1.0 / (1.0 + np.exp(-scaled.clip(-30, 30))))


@factor(
    "volume.delta_zscore_27d",
    unit="score",
    window=_THRESHOLD_WINDOW,
    inputs=("high", "low", "close", "volume"),
)
def delta_zscore_27d(frame: pd.DataFrame) -> pd.Series:
    """未过 sigmoid 的原始强度 enh/scale，保留更多区分度（方向 +1）。"""
    smoothed_delta, smooth = _smoothed_delta(frame)
    momentum = smooth.pct_change(fill_method=None) * 0.01
    enhanced = smoothed_delta * (1.0 + momentum)
    scale = smoothed_delta.abs().rolling(_THRESHOLD_WINDOW, min_periods=10).max() * _THRESHOLD_K
    return (enhanced / scale.replace(0, np.nan)).clip(-10, 10)


DELTA_FACTORS: dict[str, tuple] = {
    "volume.delta_strength_27d": (delta_strength_27d, _THRESHOLD_WINDOW, "score"),
    "volume.delta_zscore_27d": (delta_zscore_27d, _THRESHOLD_WINDOW, "score"),
}


def lookup(name: str) -> tuple:
    """返回 (fn, window, unit)；与 price/flow 的 lookup 同构。"""
    return DELTA_FACTORS[name]


__all__ = ["DELTA_FACTORS", "delta_strength_27d", "delta_zscore_27d", "lookup"]
