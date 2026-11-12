"""Tradability / limit-board pure helpers (copied verbatim from research.report.metrics)."""

from __future__ import annotations

import numpy as np
import pandas as pd


def board_limit_pct(code: str, *, is_st: bool = False) -> float:
    """按板块返回涨跌停幅度。

    ST 股票为 5%（仅能拿到**当前** ST 标记，历史 ST 状态未知，见报告局限）；
    创业板/科创板 20%；北交所 30%；其余主板 10%。
    """
    code = str(code)
    if is_st:
        return 0.05
    if code.startswith(("30", "68")):
        return 0.20
    if code.startswith(("43", "83", "87", "92")):
        return 0.30
    return 0.10


def _limit_matrix(close: pd.DataFrame, is_st: dict[str, bool] | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """由 t 日收盘推出 t+1 的涨停价与跌停价（四舍五入到分）。"""
    limits = pd.Series(
        {c: board_limit_pct(c, is_st=bool((is_st or {}).get(c, False))) for c in close.columns}
    )
    return close.mul(1 + limits, axis=1).round(2), close.mul(1 - limits, axis=1).round(2)


def tradeable_mask(
    close: pd.DataFrame,
    open_: pd.DataFrame,
    *,
    tolerance: float = 0.005,
    is_st: dict[str, bool] | None = None,
) -> pd.DataFrame:
    """``date × symbol``：t 日的信号，t+1 开盘能否买入。

    三条判定（任一不满足即不可成交）：

    1. t+1 有开盘价（停牌、退市为 NaN）；
    2. t+1 开盘价 **低于涨停价**（开盘一字涨停买不进）；
    3. 涨停价按板块规则由 t 日收盘价推出并四舍五入到分。
    """
    limit_up, _ = _limit_matrix(close, is_st)
    nxt_open = open_.shift(-1)
    return nxt_open.notna() & (nxt_open < limit_up - tolerance)


def _delayed_exit(
    close: pd.DataFrame,
    open_: pd.DataFrame,
    horizon: int,
    *,
    max_delay: int = 10,
    is_st: dict[str, bool] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """卖出端可执行性：T+h 收盘跌停/停牌时顺延到下一个可卖日。

    返回 ``(delayed_returns, delay_days, blocked_at_target)``。
    """
    dates = close.index
    n = len(dates)
    _, limit_down = _limit_matrix(close, is_st)
    limit_down = limit_down.shift(1)  # 当日跌停价由前一交易日收盘推出
    values = close.to_numpy(dtype="float64")
    open_values = open_.to_numpy(dtype="float64")
    ld = limit_down.to_numpy(dtype="float64")
    entry = np.vstack([open_values[1:], np.full((1, close.shape[1]), np.nan)])
    target = np.arange(n) + horizon
    target = np.minimum(target, n - 1)
    col_idx = np.arange(close.shape[1])

    def _blocked_at(idx: np.ndarray) -> np.ndarray:
        price = values[idx, col_idx[None, :]]
        floor = ld[idx, col_idx[None, :]]
        return (~np.isfinite(price)) | (price <= floor + 0.005)

    exit_idx = np.broadcast_to(target[:, None], (n, close.shape[1])).copy()
    blocked0 = _blocked_at(exit_idx)
    blocked = blocked0.copy()
    for _ in range(max_delay):
        bad = blocked & (exit_idx < n - 1)
        if not bad.any():
            break
        exit_idx = np.where(bad, exit_idx + 1, exit_idx)
        blocked = _blocked_at(exit_idx)
    exit_close = values[exit_idx, col_idx[None, :]]
    ret = exit_close / entry - 1
    ret[~np.isfinite(entry)] = np.nan
    delay = np.where(np.isfinite(entry), exit_idx - target[:, None], np.nan)
    # 最后一行没有 T+1 开盘，置空
    ret[-1, :] = np.nan
    delay[-1, :] = np.nan
    return (
        pd.DataFrame(ret, index=dates, columns=close.columns),
        pd.DataFrame(delay, index=dates, columns=close.columns),
        pd.DataFrame(blocked0, index=dates, columns=close.columns),
    )


def _tradeability(
    factor: pd.DataFrame,
    close: pd.DataFrame,
    open_: pd.DataFrame,
    *,
    horizon: int,
    is_st: dict[str, bool] | None,
) -> dict:
    """买入端可成交性：分子/分母逐项列清，并给出与旧口径的对账。"""
    dates = factor.index
    n = len(dates)
    limit_up, _ = _limit_matrix(close, is_st)
    nxt_open = open_.shift(-1)
    valid = factor.notna()
    has_next = pd.DataFrame(
        np.repeat((np.arange(n) < n - 1)[:, None], factor.shape[1], axis=1),
        index=dates, columns=factor.columns,
    )
    denom_mask = valid & has_next
    suspended = denom_mask & nxt_open.isna()
    limit_blocked = denom_mask & nxt_open.notna() & (nxt_open >= limit_up - 0.005)
    blocked = suspended | limit_blocked

    old_mask = tradeable_mask(close, open_, is_st=is_st)
    old_blocked = ~old_mask
    old_denom = int(old_mask.size)
    old_num = int(old_blocked.to_numpy().sum())
    new_denom = int(denom_mask.to_numpy().sum())
    new_num = int(blocked.to_numpy().sum())

    return {
        "denominator": new_denom,
        "suspended": int(suspended.to_numpy().sum()),
        "limit_up": int(limit_blocked.to_numpy().sum()),
        "blocked": new_num,
        "pct": new_num / new_denom if new_denom else np.nan,
        "old_denominator": old_denom,
        "old_blocked": old_num,
        "old_pct": old_num / old_denom if old_denom else np.nan,
        "excluded_cells": old_denom - new_denom,
        "excluded_blocked": old_num - new_num,
        "explain": (
            "旧口径分母=全部面板单元（含上市前空值、因子预热期）；"
            "新口径分母=有因子值且存在下一交易日的信号。"
        ),
    }


def _sell_side_summary(
    factor: pd.DataFrame,
    close: pd.DataFrame,
    open_: pd.DataFrame,
    planned: pd.DataFrame,
    *,
    horizon: int,
    groups: int,
    is_st: dict[str, bool] | None,
) -> dict:
    delayed, delay, blocked0 = _delayed_exit(close, open_, horizon, is_st=is_st)
    mature = pd.DataFrame(
        np.repeat((np.arange(len(factor.index)) + horizon < len(factor.index))[:, None],
                  factor.shape[1], axis=1),
        index=factor.index, columns=factor.columns,
    )
    entry_valid = open_.shift(-1).notna() & factor.notna() & mature
    blocked_target = entry_valid & blocked0
    denom = int(entry_valid.to_numpy().sum())
    # 卖出端必须按本因子实际可买入的信号统计，不能把全市场没有信号的单元格也算进来；
    # 否则不同因子的延迟笔数会逐位相同（旧实现的典型串味）。
    delay_valid = delay.where(entry_valid)
    delayed_cnt = int((delay_valid > 0).to_numpy().sum())
    stats = {
        "denominator": denom,
        "blocked_at_target": int(blocked_target.to_numpy().sum()),
        "pct_blocked": float(blocked_target.to_numpy().sum() / denom) if denom else np.nan,
        "delayed": delayed_cnt,
        "unresolved": max(0, int(blocked_target.to_numpy().sum()) - delayed_cnt),
        "pct_delayed": float(delayed_cnt / denom) if denom else np.nan,
        "avg_delay": float(delay_valid.where(delay_valid > 0).stack().mean()) if delayed_cnt else 0.0,
        "max_delay": float(delay_valid.max().max()) if delayed_cnt else 0.0,
    }
    # 组合层面：计划卖出 vs 顺延卖出的 Q5−Q1 每期均值
    def _spread(ret: pd.DataFrame) -> float:
        vals = []
        dates = factor.index
        i = 0
        while i + horizon + 1 < len(dates):
            dt = dates[i]
            row = factor.loc[dt].dropna()
            if len(row) >= groups * 5:
                ranked = row.rank(method="first")
                buckets = pd.qcut(ranked, groups, labels=False, duplicates="drop")
                top = list(row.index[buckets == groups - 1])
                bottom = list(row.index[buckets == 0])
                r = ret.loc[dt]
                t = r.reindex(top).mean()
                b = r.reindex(bottom).mean()
                if np.isfinite(t) and np.isfinite(b):
                    vals.append(float(t - b))
            i += horizon
        return float(np.mean(vals)) if vals else np.nan

    planned_spread = _spread(planned)
    delayed_spread = _spread(delayed)
    stats["spread_planned"] = planned_spread
    stats["spread_delayed"] = delayed_spread
    stats["spread_delta"] = delayed_spread - planned_spread
    return stats
