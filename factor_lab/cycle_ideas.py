"""Four fresh factors for the first Phase 0 cycle."""

from __future__ import annotations

import numpy as np
import pandas as pd

from factors.ops import price_factors

from .ideas import IdeaSpec, _clean, _ma, _ret


def _max_drawdown_20(frame: pd.DataFrame) -> pd.Series:
    close = price_factors._close(frame)
    return _clean(
        close.rolling(20, min_periods=10).apply(
            lambda values: (values / np.maximum.accumulate(values) - 1).min(), raw=True
        )
    )


def _ret_5_20(frame: pd.DataFrame) -> pd.Series:
    close = price_factors._close(frame)
    return _clean((close / close.shift(5) - 1) - (close / close.shift(20) - 1))


def _amount_accel_10_60(frame: pd.DataFrame) -> pd.Series:
    amount = price_factors._col(frame, "amount")
    ma10, ma20, ma60 = _ma(amount, 10), _ma(amount, 20), _ma(amount, 60)
    return _clean(((ma10 - ma20) - (ma20 - ma60)) / ma60.replace(0, np.nan))


def _downside_vol_20(frame: pd.DataFrame) -> pd.Series:
    ret = _ret(frame)
    return _clean(ret.where(ret < 0).rolling(20, min_periods=8).std())


CYCLE_SPECS: tuple[IdeaSpec, ...] = (
    IdeaSpec("max_drawdown_20d", "risk.max_drawdown_20d", _max_drawdown_20, 20, "ratio", -1, "最大回撤 20 日", "近期回撤代表趋势破坏。", "risk-shape"),
    IdeaSpec("ret_5_20", "momentum.ret_5_20", _ret_5_20, 20, "ratio", -1, "短长收益差 5/20", "短期相对中期收益的偏离，反转候选。", "momentum-composite"),
    IdeaSpec("amount_accel_10_60", "liquidity.amount_accel_10_60", _amount_accel_10_60, 60, "ratio", 1, "成交额加速度 10/20/60", "关注度二阶变化，正加速代表资金进入。", "liquidity-bias"),
    IdeaSpec("downside_vol_20d", "risk.downside_vol_20d", _downside_vol_20, 20, "ratio", -1, "下行波动 20 日", "短期下行波动代表尾部风险。", "risk-shape"),
)
