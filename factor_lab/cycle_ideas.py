"""Factor catalog for fresh, multi-cycle mining.

Every factor is a simple, reproducible transform of the daily price/volume
panel.  The catalog is deliberately larger than one cycle so the Scout can keep
choosing fresh names across cycles.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from factors.ops import price_factors

from .ideas import IdeaSpec, _clean, _ma, _ret


def _close(frame: pd.DataFrame) -> pd.Series:
    return price_factors._close(frame)


def _amount(frame: pd.DataFrame) -> pd.Series:
    return price_factors._col(frame, "amount")


def _turnover(frame: pd.DataFrame) -> pd.Series:
    return price_factors._turnover(frame)


def _ret_n(frame: pd.DataFrame, n: int) -> pd.Series:
    close = _close(frame)
    return _clean(close / close.shift(n) - 1)


def _rolling_drawdown(frame: pd.DataFrame, n: int) -> pd.Series:
    close = _close(frame)
    return _clean(
        close.rolling(n, min_periods=max(10, n // 3)).apply(
            lambda values: (values / np.maximum.accumulate(values) - 1).min(), raw=True
        )
    )


def _downside_vol(frame: pd.DataFrame, n: int) -> pd.Series:
    ret = _ret(frame)
    return _clean(ret.where(ret < 0).rolling(n, min_periods=max(5, n // 3)).std())


def _upside_vol(frame: pd.DataFrame, n: int) -> pd.Series:
    ret = _ret(frame)
    return _clean(ret.where(ret > 0).rolling(n, min_periods=max(5, n // 3)).std())


def _sharpe(frame: pd.DataFrame, n: int) -> pd.Series:
    ret = _ret(frame)
    mean = ret.rolling(n, min_periods=max(10, n // 3)).mean()
    std = ret.rolling(n, min_periods=max(10, n // 3)).std()
    return _clean(mean / std.replace(0, np.nan))


def _sortino(frame: pd.DataFrame, n: int) -> pd.Series:
    ret = _ret(frame)
    mean = ret.rolling(n, min_periods=max(10, n // 3)).mean()
    down = ret.where(ret < 0).rolling(n, min_periods=max(5, n // 3)).std()
    return _clean(mean / down.replace(0, np.nan))


def _ret_diff(frame: pd.DataFrame, short: int, long: int) -> pd.Series:
    return _clean(_ret_n(frame, short) - _ret_n(frame, long))


def _max_drawdown_120(frame: pd.DataFrame) -> pd.Series:
    return _rolling_drawdown(frame, 120)


def _downside_vol_120(frame: pd.DataFrame) -> pd.Series:
    return _downside_vol(frame, 120)


def _upside_vol_60(frame: pd.DataFrame) -> pd.Series:
    return _upside_vol(frame, 60)


def _sharpe_60(frame: pd.DataFrame) -> pd.Series:
    return _sharpe(frame, 60)


def _sortino_60(frame: pd.DataFrame) -> pd.Series:
    return _sortino(frame, 60)


def _ret_10_60(frame: pd.DataFrame) -> pd.Series:
    return _ret_diff(frame, 10, 60)


def _ret_21_126(frame: pd.DataFrame) -> pd.Series:
    return _ret_diff(frame, 21, 126)


def _amount_trend_5_60(frame: pd.DataFrame) -> pd.Series:
    amount = _amount(frame)
    return _clean(_ma(amount, 5) / _ma(amount, 60).replace(0, np.nan) - 1)


def _turnover_trend_5_60(frame: pd.DataFrame) -> pd.Series:
    turnover = _turnover(frame)
    return _clean(_ma(turnover, 5) / _ma(turnover, 60).replace(0, np.nan) - 1)


def _close_location_60(frame: pd.DataFrame) -> pd.Series:
    close, high, low = _close(frame), price_factors._col(frame, "high"), price_factors._col(frame, "low")
    location = (close - low) / (high - low).replace(0, np.nan)
    return _clean(location.rolling(60, min_periods=20).mean())


def _range_60(frame: pd.DataFrame) -> pd.Series:
    close, high, low = _close(frame), price_factors._col(frame, "high"), price_factors._col(frame, "low")
    return _clean(((high - low) / close.replace(0, np.nan)).rolling(60, min_periods=20).mean())


def _gap_vol_60(frame: pd.DataFrame) -> pd.Series:
    close, open_ = _close(frame), price_factors._col(frame, "open")
    gap = open_ / close.shift(1).replace(0, np.nan) - 1
    return _clean(gap.rolling(60, min_periods=20).std())


def _amihud_60(frame: pd.DataFrame) -> pd.Series:
    amount = _amount(frame).replace(0, np.nan)
    return _clean((_ret(frame).abs() / (amount / 1e8)).rolling(60, min_periods=20).mean())


def _turnover_per_amount_20(frame: pd.DataFrame) -> pd.Series:
    amount = _amount(frame).replace(0, np.nan)
    return _clean((_turnover(frame) / (amount / 1e8)).rolling(20, min_periods=8).mean())


def _ret_autocorr_60(frame: pd.DataFrame) -> pd.Series:
    ret = _ret(frame)
    return _clean(
        ret.rolling(60, min_periods=25).apply(lambda values: pd.Series(values).autocorr(), raw=True)
    )


def _vol_of_vol_120(frame: pd.DataFrame) -> pd.Series:
    vol = _ret(frame).rolling(20, min_periods=10).std()
    return _clean(vol.rolling(120, min_periods=40).std())


def _high_low_ratio_60(frame: pd.DataFrame) -> pd.Series:
    high, low = price_factors._col(frame, "high"), price_factors._col(frame, "low")
    return _clean((high / low.replace(0, np.nan) - 1).rolling(60, min_periods=20).mean())


def _amount_vol_ratio_5_20(frame: pd.DataFrame) -> pd.Series:
    amount = _amount(frame)
    return _clean(amount.rolling(5, min_periods=3).std() / amount.rolling(20, min_periods=8).std().replace(0, np.nan) - 1)


CYCLE_SPECS: tuple[IdeaSpec, ...] = (
    IdeaSpec("max_drawdown_20d", "risk.max_drawdown_20d", lambda f: _rolling_drawdown(f, 20), 20, "ratio", -1, "最大回撤 20 日", "近期回撤代表趋势破坏。", "risk-shape"),
    IdeaSpec("ret_5_20", "momentum.ret_5_20", lambda f: _ret_diff(f, 5, 20), 20, "ratio", -1, "短长收益差 5/20", "短期相对中期收益的偏离，反转候选。", "momentum-composite"),
    IdeaSpec("amount_accel_10_60", "liquidity.amount_accel_10_60", lambda f: _clean(((_ma(_amount(f), 10) - _ma(_amount(f), 20)) - (_ma(_amount(f), 20) - _ma(_amount(f), 60))) / _ma(_amount(f), 60).replace(0, np.nan)), 60, "ratio", 1, "成交额加速度 10/20/60", "关注度二阶变化，正加速代表资金进入。", "liquidity-bias"),
    IdeaSpec("downside_vol_20d", "risk.downside_vol_20d", lambda f: _downside_vol(f, 20), 20, "ratio", -1, "下行波动 20 日", "短期下行波动代表尾部风险。", "risk-shape"),
    IdeaSpec("max_drawdown_120d", "risk.max_drawdown_120d", _max_drawdown_120, 120, "ratio", -1, "最大回撤 120 日", "中期回撤代表趋势与风险预算破坏。", "risk-shape"),
    IdeaSpec("downside_vol_120d", "risk.downside_vol_120d", _downside_vol_120, 120, "ratio", -1, "下行波动 120 日", "中期下行波动代表尾部风险。", "risk-shape"),
    IdeaSpec("upside_vol_60d", "risk.upside_vol_60d", _upside_vol_60, 60, "ratio", -1, "上行波动 60 日", "上行波动过高代表拥挤与回撤风险。", "risk-shape"),
    IdeaSpec("sharpe_60d", "risk.sharpe_60d", _sharpe_60, 60, "ratio", 1, "Sharpe 60 日", "风险调整后收益，趋势质量候选。", "risk-shape"),
    IdeaSpec("sortino_60d", "risk.sortino_60d", _sortino_60, 60, "ratio", 1, "Sortino 60 日", "下行风险调整后收益。", "risk-shape"),
    IdeaSpec("ret_10_60", "momentum.ret_10_60", _ret_10_60, 60, "ratio", -1, "短长收益差 10/60", "短期相对中期收益偏离，反转候选。", "momentum-composite"),
    IdeaSpec("ret_21_126", "momentum.ret_21_126", _ret_21_126, 126, "ratio", -1, "短长收益差 21/126", "月度相对半年收益偏离，反转候选。", "momentum-composite"),
    IdeaSpec("amount_trend_5_60", "liquidity.amount_trend_5_60", _amount_trend_5_60, 60, "ratio", 1, "成交额趋势 5/60", "短期成交额相对中期抬升，关注度上行。", "liquidity-bias"),
    IdeaSpec("turnover_trend_5_60", "liquidity.turnover_trend_5_60", _turnover_trend_5_60, 60, "ratio", -1, "换手趋势 5/60", "短期换手相对中期抬升，拥挤候选。", "liquidity-bias"),
    IdeaSpec("close_location_60d", "momentum.close_location_60d", _close_location_60, 60, "ratio", 1, "收盘位置 60 日", "收盘长期位于日内高位，买盘承接。", "momentum-shape"),
    IdeaSpec("range_60d", "momentum.range_60d", _range_60, 60, "ratio", -1, "日内振幅 60 日", "中期振幅代表不确定性与彩票偏好。", "momentum-shape"),
    IdeaSpec("gap_vol_60d", "risk.gap_vol_60d", _gap_vol_60, 60, "ratio", -1, "隔夜跳空波动 60 日", "中期隔夜冲击风险。", "risk-shape"),
    IdeaSpec("amihud_60d", "liquidity.amihud_60d", _amihud_60, 60, "ratio", 1, "Amihud 非流动性 60 日", "中期价格冲击，流动性溢价候选。", "liquidity-amihud"),
    IdeaSpec("turnover_per_amount_20d", "liquidity.turnover_per_amount_20d", _turnover_per_amount_20, 20, "ratio", -1, "单位成交额换手 20 日", "单位成交额换手偏高，交易拥挤候选。", "liquidity-amihud"),
    IdeaSpec("ret_autocorr_60d", "momentum.ret_autocorr_60d", _ret_autocorr_60, 60, "ratio", -1, "收益自相关 60 日", "中期收益正自相关代表趋势拥挤。", "momentum-shape"),
    IdeaSpec("vol_of_vol_120d", "risk.vol_of_vol_120d", _vol_of_vol_120, 120, "ratio", -1, "波动率的波动 120 日", "长期波动状态不稳定，风险溢价候选。", "risk-shape"),
    IdeaSpec("high_low_ratio_60d", "risk.high_low_ratio_60d", _high_low_ratio_60, 60, "ratio", -1, "高低价比 60 日", "中期区间宽度代表不确定性与彩票偏好。", "risk-shape"),
    IdeaSpec("amount_vol_ratio_5_20", "liquidity.amount_vol_ratio_5_20", _amount_vol_ratio_5_20, 20, "ratio", -1, "成交额波动比 5/20", "短期成交额波动相对抬升，不稳定关注。", "liquidity-bias"),
)

def _upside_vol_120(frame: pd.DataFrame) -> pd.Series:
    return _upside_vol(frame, 120)


def _ret_5_126(frame: pd.DataFrame) -> pd.Series:
    return _ret_diff(frame, 5, 126)


def _amount_trend_10_60(frame: pd.DataFrame) -> pd.Series:
    amount = _amount(frame)
    return _clean(_ma(amount, 10) / _ma(amount, 60).replace(0, np.nan) - 1)


CYCLE_SPECS = CYCLE_SPECS + (
    IdeaSpec("upside_vol_120d", "risk.upside_vol_120d", _upside_vol_120, 120, "ratio", -1, "上行波动 120 日", "长期上行波动过高代表拥挤与回撤风险。", "risk-shape"),
    IdeaSpec("ret_5_126", "momentum.ret_5_126", _ret_5_126, 126, "ratio", -1, "短长收益差 5/126", "短期相对半年收益偏离，反转候选。", "momentum-composite"),
    IdeaSpec("amount_trend_10_60", "liquidity.amount_trend_10_60", _amount_trend_10_60, 60, "ratio", 1, "成交额趋势 10/60", "短期成交额相对中期抬升，关注度上行。", "liquidity-bias"),
    IdeaSpec("max_drawdown_252d", "risk.max_drawdown_252d", lambda f: _rolling_drawdown(f, 252), 252, "ratio", -1, "最大回撤 252 日", "年度回撤代表长期趋势与风险预算破坏。", "risk-shape"),
)

def _downside_vol_252(frame: pd.DataFrame) -> pd.Series:
    return _downside_vol(frame, 252)


def _ret_10_126(frame: pd.DataFrame) -> pd.Series:
    return _ret_diff(frame, 10, 126)


def _amount_trend_20_60(frame: pd.DataFrame) -> pd.Series:
    amount = _amount(frame)
    return _clean(_ma(amount, 20) / _ma(amount, 60).replace(0, np.nan) - 1)


CYCLE_SPECS = CYCLE_SPECS + (
    IdeaSpec("downside_vol_252d", "risk.downside_vol_252d", _downside_vol_252, 252, "ratio", -1, "下行波动 252 日", "年度下行波动代表长期尾部风险。", "risk-shape"),
    IdeaSpec("ret_10_126", "momentum.ret_10_126", _ret_10_126, 126, "ratio", -1, "短长收益差 10/126", "短期相对半年收益偏离，反转候选。", "momentum-composite"),
    IdeaSpec("amount_trend_20_60", "liquidity.amount_trend_20_60", _amount_trend_20_60, 60, "ratio", 1, "成交额趋势 20/60", "中期成交额相对长期抬升，关注度上行。", "liquidity-bias"),
)

def _upside_vol_252(frame: pd.DataFrame) -> pd.Series:
    return _upside_vol(frame, 252)


def _ret_21_252(frame: pd.DataFrame) -> pd.Series:
    return _ret_diff(frame, 21, 252)


CYCLE_SPECS = CYCLE_SPECS + (
    IdeaSpec("upside_vol_252d", "risk.upside_vol_252d", _upside_vol_252, 252, "ratio", -1, "上行波动 252 日", "年度上行波动代表长期拥挤与回撤风险。", "risk-shape"),
    IdeaSpec("ret_21_252", "momentum.ret_21_252", _ret_21_252, 252, "ratio", -1, "短长收益差 21/252", "月度相对年度收益偏离，反转候选。", "momentum-composite"),
)

def _parkinson_vol_20(frame: pd.DataFrame) -> pd.Series:
    high, low = price_factors._col(frame, "high"), price_factors._col(frame, "low")
    variance = (np.log(high / low.replace(0, np.nan)) ** 2).rolling(20, min_periods=8).mean()
    return _clean(np.sqrt(variance / (4.0 * np.log(2.0))))


def _cvar_60(frame: pd.DataFrame) -> pd.Series:
    ret = _ret(frame)
    def tail_loss(values):
        arr = np.asarray(values, dtype=float)
        arr = arr[np.isfinite(arr)]
        if len(arr) < 20:
            return np.nan
        k = max(1, int(len(arr) * 0.05))
        return float(-np.sort(arr)[:k].mean())
    return _clean(ret.rolling(60, min_periods=30).apply(tail_loss, raw=True))


def _vol_of_vol_60(frame: pd.DataFrame) -> pd.Series:
    return _clean(_ret(frame).rolling(20, min_periods=10).std().rolling(60, min_periods=20).std())


def _ret_3_21(frame: pd.DataFrame) -> pd.Series:
    return _ret_diff(frame, 3, 21)


def _ret_63_252(frame: pd.DataFrame) -> pd.Series:
    return _ret_diff(frame, 63, 252)


def _amount_trend_3_10(frame: pd.DataFrame) -> pd.Series:
    amount = _amount(frame)
    return _clean(_ma(amount, 3) / _ma(amount, 10).replace(0, np.nan) - 1)


def _turnover_trend_10_60(frame: pd.DataFrame) -> pd.Series:
    turnover = _turnover(frame)
    return _clean(_ma(turnover, 10) / _ma(turnover, 60).replace(0, np.nan) - 1)


def _overnight_gap_sum_5(frame: pd.DataFrame) -> pd.Series:
    close, open_ = _close(frame), price_factors._col(frame, "open")
    gap = open_ / close.shift(1).replace(0, np.nan) - 1
    return _clean(gap.rolling(5, min_periods=3).sum())


def _close_location_10(frame: pd.DataFrame) -> pd.Series:
    close, high, low = _close(frame), price_factors._col(frame, "high"), price_factors._col(frame, "low")
    location = (close - low) / (high - low).replace(0, np.nan)
    return _clean(location.rolling(10, min_periods=5).mean())


def _amihud_10(frame: pd.DataFrame) -> pd.Series:
    amount = _amount(frame).replace(0, np.nan)
    return _clean((_ret(frame).abs() / (amount / 1e8)).rolling(10, min_periods=5).mean())


CYCLE_SPECS = CYCLE_SPECS + (
    IdeaSpec("parkinson_vol_20d", "risk.parkinson_vol_20d", _parkinson_vol_20, 20, "ratio", -1, "Parkinson 波动 20 日", "高低价区间估计的波动率，低波动候选。", "risk-shape"),
    IdeaSpec("cvar_60d", "risk.cvar_60d", _cvar_60, 60, "ratio", -1, "CVaR 60 日", "5% 尾部损失，尾部风险候选。", "risk-shape"),
    IdeaSpec("vol_of_vol_60d", "risk.vol_of_vol_60d", _vol_of_vol_60, 60, "ratio", -1, "波动率的波动 60 日", "短期波动状态不稳定，风险溢价候选。", "risk-shape"),
    IdeaSpec("ret_3_21", "momentum.ret_3_21", _ret_3_21, 21, "ratio", -1, "短长收益差 3/21", "极短期相对月度收益偏离，反转候选。", "momentum-composite"),
    IdeaSpec("ret_63_252", "momentum.ret_63_252", _ret_63_252, 252, "ratio", -1, "短长收益差 63/252", "季度相对年度收益偏离，反转候选。", "momentum-composite"),
    IdeaSpec("amount_trend_3_10", "liquidity.amount_trend_3_10", _amount_trend_3_10, 10, "ratio", 1, "成交额趋势 3/10", "极短期成交额抬升，关注度上行。", "liquidity-bias"),
    IdeaSpec("turnover_trend_10_60", "liquidity.turnover_trend_10_60", _turnover_trend_10_60, 60, "ratio", -1, "换手趋势 10/60", "短期换手相对中期抬升，拥挤候选。", "liquidity-bias"),
    IdeaSpec("overnight_gap_sum_5d", "momentum.overnight_gap_sum_5d", _overnight_gap_sum_5, 5, "ratio", -1, "隔夜跳空累计 5 日", "短期隔夜情绪冲击，反转候选。", "momentum-shape"),
    IdeaSpec("close_location_10d", "momentum.close_location_10d", _close_location_10, 10, "ratio", 1, "收盘位置 10 日", "短期收盘位置强势，买盘承接。", "momentum-shape"),
    IdeaSpec("amihud_10d", "liquidity.amihud_10d", _amihud_10, 10, "ratio", 1, "Amihud 非流动性 10 日", "短期价格冲击，流动性溢价候选。", "liquidity-amihud"),
)
