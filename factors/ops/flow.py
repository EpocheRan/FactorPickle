"""情绪/资金类因子算子：两融（margin）与资金流（moneyflow）。

数据列约定
----------
回测/研究的面板帧里，这些列由数据源在 ``(symbol, date)`` 上左连接得到：

- 两融（``margin_detail``）：``margin_rzye`` / ``margin_rzmre`` / ``margin_rqye`` / ``margin_rqyl``
- 资金流（``moneyflow_daily``）：``mf_buy_sm`` / ``mf_sell_sm`` / ``mf_buy_lg`` /
  ``mf_sell_lg`` / ``mf_buy_elg`` / ``mf_sell_elg`` / ``mf_net_amount``

列缺失时返回全 NaN，而不是 KeyError——这样非两融标的、或没接该数据源的回测
会自然降级成「无信号」，而不是把整条策略打挂。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..library import factor

_EPS = 1e-12


def _col(frame: pd.DataFrame, name: str) -> pd.Series:
    if name in frame.columns:
        return pd.to_numeric(frame[name], errors="coerce").astype("float64")
    return pd.Series(np.nan, index=frame.index, dtype="float64")


def _amount(frame: pd.DataFrame) -> pd.Series:
    return _col(frame, "amount").replace(0, np.nan)


# ---------------------------------------------------------------- 两融
@factor(
    "sentiment.margin_balance_chg_20d",
    unit="ratio",
    window=20,
    inputs=("margin_rzye",),
)
def margin_balance_chg_20d(frame: pd.DataFrame) -> pd.Series:
    """融资余额 20 日变化率：杠杆盘加仓速度（拥挤度代理，方向 -1）。"""
    # 两融数据 T 日盘后披露，评估时只能用 T-1 日及以前的数据
    rzye = _col(frame, "margin_rzye").shift(1)
    return rzye / rzye.shift(20).replace(0, np.nan) - 1


@factor(
    "sentiment.margin_short_ratio",
    unit="ratio",
    window=1,
    inputs=("margin_rqye", "margin_rzye"),
)
def margin_short_ratio(frame: pd.DataFrame) -> pd.Series:
    """融券余额 / 融资余额：看空力量占杠杆盘比重（方向 -1）。"""
    rqye = _col(frame, "margin_rqye").shift(1)
    rzye = _col(frame, "margin_rzye").shift(1)
    return rqye / rzye.replace(0, np.nan)


@factor(
    "sentiment.margin_buy_ratio_20d",
    unit="ratio",
    window=20,
    inputs=("margin_rzmre", "amount"),
)
def margin_buy_ratio_20d(frame: pd.DataFrame) -> pd.Series:
    """MA20(融资买入额 / 成交额)：杠杆参与度（方向 -1）。"""
    # 同上：融资买入额滞后一日，避免用当日盘后数据
    ratio = _col(frame, "margin_rzmre").shift(1) / _amount(frame)
    return ratio.rolling(20, min_periods=10).mean()


# ---------------------------------------------------------------- 资金流
@factor(
    "sentiment.flow_large_net_ratio_20d",
    unit="ratio",
    window=20,
    inputs=("mf_buy_lg", "mf_buy_elg", "mf_sell_lg", "mf_sell_elg", "amount"),
)
def flow_large_net_ratio_20d(frame: pd.DataFrame) -> pd.Series:
    """MA20((大单+特大单净买入) / 成交额)：机构方向（方向 +1）。"""
    buy = _col(frame, "mf_buy_lg") + _col(frame, "mf_buy_elg")
    sell = _col(frame, "mf_sell_lg") + _col(frame, "mf_sell_elg")
    ratio = (buy - sell) / _amount(frame)
    return ratio.rolling(20, min_periods=10).mean()


@factor(
    "sentiment.flow_retail_net_ratio_20d",
    unit="ratio",
    window=20,
    inputs=("mf_buy_sm", "mf_sell_sm", "amount"),
)
def flow_retail_net_ratio_20d(frame: pd.DataFrame) -> pd.Series:
    """MA20((小单净买入) / 成交额)：散户接盘（方向 -1）。"""
    ratio = (_col(frame, "mf_buy_sm") - _col(frame, "mf_sell_sm")) / _amount(frame)
    return ratio.rolling(20, min_periods=10).mean()


#: 因子 id -> (函数, 窗口, 单位, 方向, 数据集)
FLOW_FACTORS: dict[str, tuple] = {
    "sentiment.margin_balance_chg_20d": (
        margin_balance_chg_20d, 20, "ratio", -1, "margin_detail",
    ),
    "sentiment.margin_short_ratio": (
        margin_short_ratio, 1, "ratio", -1, "margin_detail",
    ),
    "sentiment.margin_buy_ratio_20d": (
        margin_buy_ratio_20d, 20, "ratio", -1, "margin_detail",
    ),
    "sentiment.flow_large_net_ratio_20d": (
        flow_large_net_ratio_20d, 20, "ratio", 1, "moneyflow_daily",
    ),
    "sentiment.flow_retail_net_ratio_20d": (
        flow_retail_net_ratio_20d, 20, "ratio", -1, "moneyflow_daily",
    ),
}


def lookup(name: str) -> tuple:
    """按 factor_id 查 (fn, window, unit, direction, dataset)。"""
    try:
        return FLOW_FACTORS[name]
    except KeyError as exc:
        raise KeyError(name) from exc


__all__ = [
    "FLOW_FACTORS",
    "flow_large_net_ratio_20d",
    "flow_retail_net_ratio_20d",
    "lookup",
    "margin_balance_chg_20d",
    "margin_buy_ratio_20d",
    "margin_short_ratio",
]

# ---------------------------------------------------------------- 主力/散户净买入
@factor(
    "sentiment.main_net_buy_20d",
    unit="ratio",
    window=20,
    inputs=("mf_buy_lg", "mf_buy_elg", "mf_sell_lg", "mf_sell_elg", "amount"),
)
def main_net_buy_20d(frame: pd.DataFrame) -> pd.Series:
    """主力净买入强度：MA20((大单+超大单净额)/成交额)，方向 +1。"""
    buy = _col(frame, "mf_buy_lg") + _col(frame, "mf_buy_elg")
    sell = _col(frame, "mf_sell_lg") + _col(frame, "mf_sell_elg")
    ratio = (buy - sell) * 10000.0 / _amount(frame)   # 万元 → 元
    return ratio.rolling(20, min_periods=10).mean()


@factor(
    "sentiment.retail_net_sell_20d",
    unit="ratio",
    window=20,
    inputs=("mf_buy_sm", "mf_sell_sm", "amount"),
)
def retail_net_sell_20d(frame: pd.DataFrame) -> pd.Series:
    """散户净买入强度：MA20((小单买入-卖出)/成交额)，方向 -1（反向指标）。"""
    ratio = (_col(frame, "mf_buy_sm") - _col(frame, "mf_sell_sm")) * 10000.0 / _amount(frame)
    return ratio.rolling(20, min_periods=10).mean()


@factor(
    "sentiment.main_retail_divergence_20d",
    unit="ratio",
    window=20,
    inputs=("mf_buy_lg", "mf_buy_elg", "mf_sell_lg", "mf_sell_elg",
            "mf_buy_sm", "mf_sell_sm", "amount"),
)
def main_retail_divergence_20d(frame: pd.DataFrame) -> pd.Series:
    """主力散户背离 = 主力净买入 − 散户净买入，方向 +1。"""
    main = (_col(frame, "mf_buy_lg") + _col(frame, "mf_buy_elg")
            - _col(frame, "mf_sell_lg") - _col(frame, "mf_sell_elg"))
    retail = _col(frame, "mf_buy_sm") - _col(frame, "mf_sell_sm")
    ratio = (main - retail) * 10000.0 / _amount(frame)
    return ratio.rolling(20, min_periods=10).mean()


@factor(
    "sentiment.main_net_persistence_20d",
    unit="ratio",
    window=20,
    inputs=("mf_buy_lg", "mf_buy_elg", "mf_sell_lg", "mf_sell_elg"),
)
def main_net_persistence_20d(frame: pd.DataFrame) -> pd.Series:
    """主力净买入持续性：过去 20 日净买入天数占比，方向 +1。"""
    net = (_col(frame, "mf_buy_lg") + _col(frame, "mf_buy_elg")
           - _col(frame, "mf_sell_lg") - _col(frame, "mf_sell_elg"))
    return (net > 0).astype(float).rolling(20, min_periods=10).mean()

# 新因子在文件末尾定义，注册需放在定义之后
FLOW_FACTORS.update({
    "sentiment.main_net_buy_20d": (main_net_buy_20d, 20, "ratio", +1, "moneyflow_daily"),
    "sentiment.retail_net_sell_20d": (retail_net_sell_20d, 20, "ratio", -1, "moneyflow_daily"),
    "sentiment.main_retail_divergence_20d": (main_retail_divergence_20d, 20, "ratio", +1, "moneyflow_daily"),
    "sentiment.main_net_persistence_20d": (main_net_persistence_20d, 20, "ratio", +1, "moneyflow_daily"),
})
