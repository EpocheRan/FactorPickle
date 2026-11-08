"""A first batch of 24 price/volume factor hypotheses.

These are intentionally simple, reproducible transforms of the daily panel.  The
batch is a mining sweep, not a claim that all 24 should survive; the audit and
G0.5 gates decide.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np
import pandas as pd

from factors.ops import price_factors

from .schemas import Hypothesis, OutcomeSpec, new_hypothesis_id

UNIVERSE = "pool:2022-01-04:200"
START = "2019-01-01"
END = "2026-11-01"


def _amount(frame: pd.DataFrame) -> pd.Series:
    return price_factors._col(frame, "amount")


def _ret(frame: pd.DataFrame) -> pd.Series:
    return price_factors._close(frame).pct_change(fill_method=None)


def _turnover(frame: pd.DataFrame) -> pd.Series:
    return price_factors._turnover(frame)


def _ma(series: pd.Series, n: int) -> pd.Series:
    return series.rolling(n, min_periods=max(2, n // 3)).mean()


def _std(series: pd.Series, n: int) -> pd.Series:
    return series.rolling(n, min_periods=max(5, n // 3)).std()


def _bias(short: pd.Series, long: pd.Series) -> pd.Series:
    return (short / long.replace(0, np.nan) - 1).replace([np.inf, -np.inf], np.nan)


def _clean(series: pd.Series) -> pd.Series:
    return series.replace([np.inf, -np.inf], np.nan)


def _amount_bias(short: int, long: int) -> Callable[[pd.DataFrame], pd.Series]:
    def fn(frame: pd.DataFrame) -> pd.Series:
        amount = _amount(frame)
        return _bias(_ma(amount, short), _ma(amount, long))

    return fn


def _turnover_accel(frame: pd.DataFrame) -> pd.Series:
    turnover = _turnover(frame)
    ma5, ma20, ma60 = _ma(turnover, 5), _ma(turnover, 20), _ma(turnover, 60)
    return _clean(((ma5 - ma20) - (ma20 - ma60)) / ma60.replace(0, np.nan))


def _amount_accel(frame: pd.DataFrame) -> pd.Series:
    amount = _amount(frame)
    ma5, ma20, ma60 = _ma(amount, 5), _ma(amount, 20), _ma(amount, 60)
    return _clean(((ma5 - ma20) - (ma20 - ma60)) / ma60.replace(0, np.nan))


def _amount_share(frame: pd.DataFrame) -> pd.Series:
    amount = _amount(frame)
    return _bias(_ma(amount, 5), _ma(amount, 20))


def _turnover_share(frame: pd.DataFrame) -> pd.Series:
    turnover = _turnover(frame)
    return _bias(_ma(turnover, 5), _ma(turnover, 20))


def _amount_vol_ratio(frame: pd.DataFrame) -> pd.Series:
    amount = _amount(frame)
    return _clean(_std(amount, 20) / _std(amount, 60).replace(0, np.nan) - 1)


def _turnover_vol_ratio(frame: pd.DataFrame) -> pd.Series:
    turnover = _turnover(frame)
    return _clean(_std(turnover, 20) / _std(turnover, 60).replace(0, np.nan) - 1)


def _amihud(frame: pd.DataFrame) -> pd.Series:
    amount = _amount(frame).replace(0, np.nan)
    return _clean((_ret(frame).abs() / (amount / 1e8)).rolling(20, min_periods=8).mean())


def _ret_skew(frame: pd.DataFrame) -> pd.Series:
    return _clean(_ret(frame).rolling(20, min_periods=10).skew())


def _ret_kurt(frame: pd.DataFrame) -> pd.Series:
    return _clean(_ret(frame).rolling(60, min_periods=20).kurt())


def _ret_autocorr(frame: pd.DataFrame) -> pd.Series:
    return _clean(
        _ret(frame).rolling(20, min_periods=10).apply(
            lambda values: pd.Series(values).autocorr(), raw=True
        )
    )


def _intraday_strength(frame: pd.DataFrame) -> pd.Series:
    close = price_factors._close(frame)
    open_ = price_factors._col(frame, "open")
    return _clean(((close - open_) / open_.replace(0, np.nan)).rolling(20, min_periods=8).mean())


def _overnight_gap(frame: pd.DataFrame) -> pd.Series:
    close = price_factors._close(frame)
    open_ = price_factors._col(frame, "open")
    gap = open_ / close.shift(1).replace(0, np.nan) - 1
    return _clean(gap.rolling(20, min_periods=8).mean())


def _close_location(frame: pd.DataFrame) -> pd.Series:
    close = price_factors._close(frame)
    high = price_factors._col(frame, "high")
    low = price_factors._col(frame, "low")
    location = (close - low) / (high - low).replace(0, np.nan)
    return _clean(location.rolling(20, min_periods=8).mean())


def _range(frame: pd.DataFrame) -> pd.Series:
    close = price_factors._close(frame)
    high = price_factors._col(frame, "high")
    low = price_factors._col(frame, "low")
    return _clean(((high - low) / close.replace(0, np.nan)).rolling(20, min_periods=8).mean())


def _downside_vol(frame: pd.DataFrame) -> pd.Series:
    ret = _ret(frame)
    return _clean(ret.where(ret < 0).rolling(60, min_periods=20).std())


def _updown_vol_ratio(frame: pd.DataFrame) -> pd.Series:
    ret = _ret(frame)
    up = ret.where(ret > 0).rolling(60, min_periods=20).std()
    down = ret.where(ret < 0).rolling(60, min_periods=20).std()
    return _clean(up / down.replace(0, np.nan) - 1)


def _max_drawdown(frame: pd.DataFrame) -> pd.Series:
    close = price_factors._close(frame)
    return _clean(
        close.rolling(60, min_periods=20).apply(
            lambda values: (values / np.maximum.accumulate(values) - 1).min(), raw=True
        )
    )


def _vol_of_vol(frame: pd.DataFrame) -> pd.Series:
    return _clean(_ret(frame).rolling(20, min_periods=10).std().rolling(60, min_periods=20).std())


def _gap_vol(frame: pd.DataFrame) -> pd.Series:
    close = price_factors._close(frame)
    open_ = price_factors._col(frame, "open")
    gap = open_ / close.shift(1).replace(0, np.nan) - 1
    return _clean(gap.rolling(20, min_periods=8).std())


def _high_low_ratio(frame: pd.DataFrame) -> pd.Series:
    high = price_factors._col(frame, "high")
    low = price_factors._col(frame, "low")
    return _clean((high / low.replace(0, np.nan) - 1).rolling(20, min_periods=8).mean())


def _ret_5_63(frame: pd.DataFrame) -> pd.Series:
    close = price_factors._close(frame)
    return _clean((close / close.shift(5) - 1) - (close / close.shift(63) - 1))


def _amount_trend_consistency(frame: pd.DataFrame) -> pd.Series:
    amount = _amount(frame)
    above = (amount > _ma(amount, 20)).astype(float)
    return _clean(above.rolling(20, min_periods=10).mean())


@dataclass(frozen=True)
class IdeaSpec:
    key: str
    factor_id: str
    fn: Callable[[pd.DataFrame], pd.Series]
    window: int
    unit: str
    direction: int
    title: str
    mechanism: str
    bet: str

    def hypothesis(self) -> Hypothesis:
        return Hypothesis(
            hypothesis_id=new_hypothesis_id(self.factor_id),
            factor_id=self.factor_id,
            title=self.title,
            mechanism=self.mechanism,
            expected_sign=self.direction,
            outcomes=[
                OutcomeSpec(
                    universe=UNIVERSE,
                    horizon=5,
                    direction=self.direction,
                    groups=5,
                    cost_bps=20.0,
                )
            ],
            outcome_policy="single",
            data=["ohlcv_1d.amount", "ohlcv_1d.close", "ohlcv_1d.high", "ohlcv_1d.low", "ohlcv_1d.open"],
            kill_criteria=[
                "coverage < 0.6",
                "turnover_5d > policy.max_turnover_5d",
                "capacity < 3x target",
                "audit hard fail",
                "G0.5 correlation CI crosses hard threshold",
            ],
            source="factor_lab.ideas",
            staging=self.factor_id,
            bet=self.bet,
            start=START,
            end=END,
            notes="Mining batch; single-horse, quick profile.",
        )


IDEA_SPECS: tuple[IdeaSpec, ...] = (
    IdeaSpec("amount_bias_21_252", "liquidity.amount_bias_21_252", _amount_bias(21, 252), 252, "ratio", 1, "成交额二阶化 21/252", "短期成交额相对长期抬升，代表关注度变化。", "liquidity-bias"),
    IdeaSpec("amount_bias_63_252", "liquidity.amount_bias_63_252", _amount_bias(63, 252), 252, "ratio", 1, "成交额二阶化 63/252", "中期成交额相对长期抬升，代表资金关注度变化。", "liquidity-bias"),
    IdeaSpec("amount_accel", "liquidity.amount_accel_5_20_60", _amount_accel, 60, "ratio", 1, "成交额加速度", "短期成交额加速相对中期加速，代表关注度二阶变化。", "liquidity-bias"),
    IdeaSpec("turnover_accel", "liquidity.turnover_accel_5_20_60", _turnover_accel, 60, "ratio", -1, "换手加速度", "换手加速代表拥挤与情绪升温，预期未来收益偏低。", "liquidity-bias"),
    IdeaSpec("amount_share", "liquidity.amount_share_5_20", _amount_share, 20, "ratio", 1, "成交额短期占比", "5 日成交额相对 20 日抬升，代表关注度上升。", "liquidity-bias"),
    IdeaSpec("turnover_share", "liquidity.turnover_share_5_20", _turnover_share, 20, "ratio", -1, "换手短期占比", "短期换手相对抬升，代表交易拥挤。", "liquidity-bias"),
    IdeaSpec("amount_vol_ratio", "liquidity.amount_vol_ratio_20_60", _amount_vol_ratio, 60, "ratio", -1, "成交额波动比", "短期成交额波动相对长期抬升，代表不稳定关注。", "liquidity-bias"),
    IdeaSpec("turnover_vol_ratio", "liquidity.turnover_vol_ratio_20_60", _turnover_vol_ratio, 60, "ratio", -1, "换手波动比", "短期换手波动相对抬升，代表交易状态不稳定。", "liquidity-bias"),
    IdeaSpec("amihud_20d", "liquidity.amihud_20d", _amihud, 20, "ratio", 1, "Amihud 非流动性", "单位成交额带来的价格冲击，流动性溢价候选。", "liquidity-amihud"),
    IdeaSpec("ret_skew_20d", "momentum.ret_skew_20d", _ret_skew, 20, "ratio", -1, "收益偏度 20 日", "正偏度代表彩票偏好，预期未来收益偏低。", "momentum-shape"),
    IdeaSpec("ret_kurt_60d", "momentum.ret_kurt_60d", _ret_kurt, 60, "ratio", -1, "收益峰度 60 日", "高峰度代表尾部风险与彩票偏好。", "momentum-shape"),
    IdeaSpec("ret_autocorr_20d", "momentum.ret_autocorr_20d", _ret_autocorr, 20, "ratio", -1, "收益自相关 20 日", "短期收益正自相关代表趋势拥挤，反转候选。", "momentum-shape"),
    IdeaSpec("intraday_strength_20d", "momentum.intraday_strength_20d", _intraday_strength, 20, "ratio", 1, "日内强度 20 日", "收盘相对开盘的强势，代表日内买盘持续。", "momentum-shape"),
    IdeaSpec("overnight_gap_20d", "momentum.overnight_gap_20d", _overnight_gap, 20, "ratio", -1, "隔夜跳空 20 日", "隔夜跳空代表情绪冲击，均值回复候选。", "momentum-shape"),
    IdeaSpec("close_location_20d", "momentum.close_location_20d", _close_location, 20, "ratio", 1, "收盘位置 20 日", "收盘位于日内区间高位，代表买盘承接。", "momentum-shape"),
    IdeaSpec("range_20d", "momentum.range_20d", _range, 20, "ratio", -1, "日内振幅 20 日", "高振幅代表不确定性与彩票偏好。", "momentum-shape"),
    IdeaSpec("downside_vol_60d", "risk.downside_vol_60d", _downside_vol, 60, "ratio", -1, "下行波动 60 日", "下行波动代表尾部风险，低波动溢价候选。", "risk-shape"),
    IdeaSpec("updown_vol_ratio_60d", "risk.updown_vol_ratio_60d", _updown_vol_ratio, 60, "ratio", -1, "上下行波动比 60 日", "上行波动相对下行抬升，代表风险补偿不足。", "risk-shape"),
    IdeaSpec("max_drawdown_60d", "risk.max_drawdown_60d", _max_drawdown, 60, "ratio", -1, "最大回撤 60 日", "近期最大回撤代表趋势破坏。", "risk-shape"),
    IdeaSpec("vol_of_vol_60d", "risk.vol_of_vol_60d", _vol_of_vol, 60, "ratio", -1, "波动率的波动 60 日", "波动状态不稳定，代表风险溢价。", "risk-shape"),
    IdeaSpec("gap_vol_20d", "risk.gap_vol_20d", _gap_vol, 20, "ratio", -1, "隔夜跳空波动 20 日", "隔夜跳空波动代表信息冲击风险。", "risk-shape"),
    IdeaSpec("high_low_ratio_20d", "risk.high_low_ratio_20d", _high_low_ratio, 20, "ratio", -1, "高低价比 20 日", "日内区间宽度代表不确定性与彩票偏好。", "risk-shape"),
    IdeaSpec("ret_5_63", "momentum.ret_5_63", _ret_5_63, 63, "ratio", -1, "短长收益差", "短期相对长期收益的偏离，反转候选。", "momentum-composite"),
    IdeaSpec("amount_trend_consistency", "liquidity.amount_trend_consistency_20d", _amount_trend_consistency, 20, "ratio", 1, "成交额趋势一致性", "成交额持续高于自身均线，代表关注度持续。", "liquidity-bias"),
)


def idea_for(factor_id: str) -> IdeaSpec | None:
    for spec in IDEA_SPECS:
        if spec.factor_id == factor_id:
            return spec
    return None


def all_idea_hypotheses() -> list[Hypothesis]:
    return [spec.hypothesis() for spec in IDEA_SPECS]
