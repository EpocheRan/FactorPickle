"""通用数据质量与覆盖诊断。

这里不绑定任何具体因子：清洗标准、覆盖漏斗、零值构成都是对
``factor × close × open × low × amount`` 的统一检查。报告层只负责展示，
评级层只根据诊断结果决定是否采用清洗后的口径。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd


#: 零值桶触发阈值（先给保守默认，后续用全库审计校准）
ZERO_SHARE_TRIGGER = 0.15
ZERO_NO_DATA_TRIGGER = 0.10


@dataclass(frozen=True)
class CleaningStandard:
    """报告主口径的观测层清洗标准。

    只删单元格，不删股票：上市前、历史不足、停牌/无成交、无下一日开盘的
    观测不参与排序；ST、低价、小市值、低流动性只要当天有效就保留。
    """

    min_history: int = 20
    exclude_bse: bool = True
    exclude_suspended: bool = True
    exclude_no_next_open: bool = True
    zero_share_trigger: float = ZERO_SHARE_TRIGGER
    zero_no_data_trigger: float = ZERO_NO_DATA_TRIGGER
    label: str = "全 A 剔北证（有效观测）"

    def describe(self) -> dict:
        return {
            "label": self.label,
            "min_history": int(self.min_history),
            "exclude_bse": bool(self.exclude_bse),
            "exclude_suspended": bool(self.exclude_suspended),
            "exclude_no_next_open": bool(self.exclude_no_next_open),
            "zero_share_trigger": float(self.zero_share_trigger),
            "zero_no_data_trigger": float(self.zero_no_data_trigger),
            "text": (
                f"剔除北证；剔除上市前/无收盘、不足 {self.min_history} 日历史、"
                "信号日停牌/无成交、无下一日开盘的单元格；"
                "ST、低价、小市值、低流动性只要当天有效即保留"
            ),
        }


@dataclass
class DataQuality:
    """清洗掩码 + 可序列化摘要。"""

    mask: pd.DataFrame
    summary: dict = field(default_factory=dict)


def _bse_columns(columns: pd.Index) -> pd.Series:
    codes = pd.Index([str(c) for c in columns])
    return pd.Series(codes.str.startswith(("4", "8", "9")), index=columns, dtype=bool)


def _empty_bool_like(frame: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame(False, index=frame.index, columns=frame.columns)


def analyze(
    factor: pd.DataFrame,
    close: pd.DataFrame,
    open_: pd.DataFrame,
    *,
    low: pd.DataFrame | None = None,
    amount: pd.DataFrame | None = None,
    standard: CleaningStandard | None = None,
    horizon: int = 5,
) -> DataQuality:
    """计算清洗掩码与覆盖率/零值诊断。

    返回的 ``mask`` 是 date × symbol 的布尔表；``summary`` 只含
    Python 标量/列表/字典，可直接写入报告元数据。
    """
    std = standard or CleaningStandard()
    columns = factor.columns
    index = factor.index
    close = close.reindex(index=index, columns=columns)
    open_ = open_.reindex(index=index, columns=columns)
    low = (low if low is not None else close).reindex(index=index, columns=columns)
    amount = amount.reindex(index=index, columns=columns) if amount is not None else None

    bse = _bse_columns(columns)
    bse_frame = pd.DataFrame(
        np.tile(bse.to_numpy(), (len(index), 1)), index=index, columns=columns
    )
    universe = ~bse_frame if std.exclude_bse else pd.DataFrame(
        True, index=index, columns=columns
    )

    has_price = close.notna()
    hist = low.notna().rolling(int(std.min_history), min_periods=1).sum() >= int(
        std.min_history
    )
    suspended = (
        (amount.isna() | (amount <= 0))
        if (amount is not None and std.exclude_suspended)
        else _empty_bool_like(factor)
    )
    next_open = open_.shift(-1).notna() if std.exclude_no_next_open else pd.DataFrame(
        True, index=index, columns=columns
    )

    valid = universe & has_price & hist & ~suspended & next_open
    signal = factor.notna()
    valid_signal = valid & signal
    zero = signal & (factor == 0)
    zero_no_data = zero & (~has_price | ~hist | suspended | ~next_open)
    zero_true = zero & valid

    finite = int(signal.sum().sum())
    cells = int(factor.size)
    zero_share = float(zero.sum().sum() / finite) if finite else float("nan")
    zero_no_data_share = (
        float(zero_no_data.sum().sum() / finite) if finite else float("nan")
    )
    triggered = bool(
        np.isfinite(zero_share)
        and np.isfinite(zero_no_data_share)
        and zero_share >= std.zero_share_trigger
        and zero_no_data_share >= std.zero_no_data_trigger
    )

    # G1 构成：与报告分组同一 rank(method="first") 口径，每 horizon 取一期
    g1_zero = []
    g1_no_data = []
    g1_true = []
    g1_positive = []
    i = 0
    while i + horizon + 1 < len(index):
        row = factor.iloc[i]
        ok = row.notna()
        if int(ok.sum()) >= 25:
            vals = row[ok]
            order = np.argsort(vals.to_numpy(), kind="stable")
            n = len(vals)
            take = max(1, n // 5)
            cols = vals.index[order[:take]]
            pos = factor.columns.get_indexer(cols)
            z = zero.iloc[i].to_numpy()[pos]
            nd = zero_no_data.iloc[i].to_numpy()[pos]
            zt = zero_true.iloc[i].to_numpy()[pos]
            g1_zero.append(float(z.mean()))
            g1_no_data.append(float(nd.mean()))
            g1_true.append(float(zt.mean()))
            g1_positive.append(float((~z).mean()))
        i += horizon

    def _avg(values: list[float]) -> float:
        return float(np.mean(values)) if values else float("nan")

    zero_by_year = (
        zero.mean(axis=1).groupby(index.year).mean().mul(100).round(2).to_dict()
    )
    funnel_steps = [
        ("全样本单元格", cells),
        ("剔除北证", int(universe.sum().sum())),
        ("有收盘价", int((universe & has_price).sum().sum())),
        ("满历史", int((universe & has_price & hist).sum().sum())),
        ("非停牌", int((universe & has_price & hist & ~suspended).sum().sum())),
        ("有下一日开盘", int(valid.sum().sum())),
        ("有信号", int(valid_signal.sum().sum())),
    ]
    funnel_base = funnel_steps[0][1] or 1
    funnel = [
        {"step": name, "count": int(count), "pct": float(count / funnel_base)}
        for name, count in funnel_steps
    ]

    _finite_factor = factor.to_numpy(dtype="float64")
    _finite_factor = _finite_factor[np.isfinite(_finite_factor)]
    _skew = float(pd.Series(_finite_factor).skew()) if len(_finite_factor) else float("nan")
    _p1 = float(np.quantile(_finite_factor, 0.01)) if len(_finite_factor) else float("nan")
    _p99 = float(np.quantile(_finite_factor, 0.99)) if len(_finite_factor) else float("nan")

    _symbol_total = int(factor.shape[1])
    _symbol_universe = int(universe.any(axis=0).sum())
    _symbol_valid = int(valid_signal.any(axis=0).sum())
    summary = {
        "standard": std.describe(),
        "symbol_funnel": [
            {"step": "universe 解析", "count": _symbol_total, "note": "all_a"},
            {"step": "剔除北证", "count": _symbol_universe,
             "note": f"−{_symbol_total - _symbol_universe}"},
            {"step": "参与排序", "count": _symbol_valid,
             "note": f"主口径 {_symbol_universe:,} 只；其中 {_symbol_universe - _symbol_valid} 只全期无有效观测，"
                     f"不参与排序；日均有效 {float(valid_signal.sum(axis=1).mean()):,.0f} 只"},
        ],
        "universe_symbols": _symbol_universe,
        "coverage": {
            "cells": cells,
            "raw_signal": float(signal.sum().sum() / cells) if cells else float("nan"),
            "price": float(has_price.sum().sum() / cells) if cells else float("nan"),
            "valid_obs": (
                float(valid.sum().sum() / cells) if cells else float("nan")
            ),
            "valid_signal": float(valid_signal.sum().sum() / cells)
            if cells
            else float("nan"),
            "valid_signal_ratio": (
                float(valid_signal.sum().sum() / valid.sum().sum())
                if valid.sum().sum() else float("nan")
            ),
            "avg_names": float(valid_signal.sum(axis=1).mean()),
            "min_names": int(valid_signal.sum(axis=1).min()),
            "max_names": int(valid_signal.sum(axis=1).max()),
        },
        "zero": {
            "share": zero_share,
            "no_data_share": zero_no_data_share,
            "true_share": float(zero_true.sum().sum() / finite) if finite else float("nan"),
            "g1_zero": _avg(g1_zero),
            "g1_no_data": _avg(g1_no_data),
            "g1_true": _avg(g1_true),
            "g1_positive": _avg(g1_positive),
            "by_year": {str(k): float(v) for k, v in zero_by_year.items()},
        },
        "factor": {"skew": _skew, "p1": _p1, "p99": _p99},
        "funnel": funnel,
        "triggered": triggered,
        "reasons": [],
        "clean": {},
    }
    if triggered:
        summary["reasons"].append(
            "零值桶污染：零值占比 {:.1%}，其中无数据零值 {:.1%}".format(
                zero_share, zero_no_data_share
            )
        )
    if np.isfinite(_skew) and abs(_skew) > 20:
        summary["reasons"].append(
            f"因子分布偏度 {_skew:.1f}，存在极端值；IC 用秩相关不受影响，"
            "分层收益与容量估计需结合缩尾/核验"
        )
    return DataQuality(mask=valid, summary=summary)


__all__ = ["CleaningStandard", "DataQuality", "analyze"]
