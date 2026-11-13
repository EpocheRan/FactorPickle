"""Quantile portfolio NAV engine (copied verbatim from research.report.metrics).

成本口径（research 校准口径）：``cost_bps`` 为**单腿往返**费率（买+卖），
按该组换手率摊在调仓日：``cost = turnover_g × cost_bps``；多空组合两条腿各计
一次：``(turnover_Q5 + turnover_Q1) × cost_bps``。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class QuantileResult:
    nav: pd.DataFrame
    turnover_df: pd.DataFrame
    periods_net: pd.DataFrame
    periods_gross: pd.DataFrame
    daily: pd.DataFrame
    ls_cost: pd.Series
    turnover_mean: pd.Series

    def __iter__(self):
        return iter((
            self.nav, self.turnover_df, self.periods_net, self.periods_gross,
            self.daily, self.ls_cost, self.turnover_mean,
        ))

    def __getitem__(self, item):
        return tuple(self)[item]


def _quantile_nav(
    factor: pd.DataFrame,
    close: pd.DataFrame,
    open_: pd.DataFrame,
    *,
    groups: int,
    horizon: int,
    cost_bps: float = 20.0,
    tradeable: pd.DataFrame | None = None,
) -> QuantileResult:
    """分组等权组合：日度净值 + 每期收益（毛/净）+ 日收益 + 换手。

    成本口径：``cost_bps`` 为**单腿往返**费率（买+卖），按该组换手率摊在调仓日：
    ``cost = turnover_g × cost_bps``。多空组合两条腿各计一次：
    ``(turnover_Q5 + turnover_Q1) × cost_bps``。

    返回 ``(nav, turnover_df, periods_net, periods_gross, daily_gross, ls_cost, turnover_mean)``。
    """
    from .backend import resolve_quantile_backend

    if resolve_quantile_backend(int(factor.shape[0]) * int(factor.shape[1])) == "polars":
        from .backends.polars import quantile_nav as _polars_quantile_nav

        return _polars_quantile_nav(
            factor,
            close,
            open_,
            groups=groups,
            horizon=horizon,
            cost_bps=cost_bps,
            tradeable=tradeable,
        )
    dates = factor.index
    labels = [f"Q{i + 1}" for i in range(groups)]
    nav = pd.DataFrame(np.nan, index=dates, columns=labels)
    daily = pd.DataFrame(0.0, index=dates, columns=labels)
    ls_cost = pd.Series(0.0, index=dates)
    periods_net: list[dict[str, float]] = []
    periods_gross: list[dict[str, float]] = []
    period_dates: list[pd.Timestamp] = []
    turnover_rows: list[dict[str, float]] = []
    prev_members: dict[int, set[str]] = {g: set() for g in range(groups)}
    level = {g: 1.0 for g in range(groups)}
    # P3：一次性转 numpy，内层不再逐日构造 Series（等价口径，收益计算不变）
    _close_np = close.to_numpy(dtype="float64")
    _open_np = open_.to_numpy(dtype="float64")
    _col_pos = {str(code): pos for pos, code in enumerate(close.columns)}
    i = 0
    while i + horizon + 1 < len(dates):
        dt = dates[i]
        row = factor.loc[dt]
        valid = row.dropna()
        if tradeable is not None and dt in tradeable.index:
            ok = tradeable.loc[dt].reindex(valid.index).fillna(False)
            valid = valid[ok]
        if len(valid) >= groups * 5:
            ranked = valid.rank(method="first")
            buckets = pd.qcut(ranked, groups, labels=False, duplicates="drop")
            members = {g: list(valid.index[buckets == g]) for g in range(groups)}
            turnover = {}
            for g in range(groups):
                current = set(members[g])
                turnover[g] = len(current - prev_members[g]) / max(1, len(current))
                prev_members[g] = current
            turnover_rows.append({f"Q{g + 1}": turnover[g] for g in range(groups)})

            period_gross = {g: 1.0 for g in range(groups)}
            period_net = {g: 1.0 for g in range(groups)}
            # 预先映射成分代码 -> 列下标，内层只做 numpy 取值（等价于逐日 pandas 索引，
            # 但避免每期 5×horizon 次 Series 构造，是 _quantile_nav 的主要开销）
            member_idx = {
                g: np.array([_col_pos[c] for c in members[g] if c in _col_pos], dtype=np.intp)
                for g in range(groups)
            }
            for j in range(i + 1, i + 1 + horizon):
                if j >= len(dates):
                    break
                for g in range(groups):
                    idx = member_idx[g]
                    if idx.size == 0:
                        continue
                    if j == i + 1:
                        px_in = _open_np[j, idx]
                        with np.errstate(invalid="ignore", divide="ignore"):
                            ratio = np.clip(_close_np[j, idx] / px_in - 1.0, -0.30, 0.30)
                        ret = float(np.nanmean(np.where(np.isfinite(ratio), ratio, np.nan)))
                        cost = turnover[g] * cost_bps / 10000.0
                        net = ret - cost
                    else:
                        with np.errstate(invalid="ignore", divide="ignore"):
                            ratio = np.clip(_close_np[j, idx] / _close_np[j - 1, idx] - 1.0, -0.30, 0.30)
                        ret = float(np.nanmean(np.where(np.isfinite(ratio), ratio, np.nan)))
                        net = ret
                    if not np.isfinite(ret):
                        ret, net = 0.0, 0.0
                    period_gross[g] *= 1 + ret
                    period_net[g] *= 1 + net
                    level[g] *= 1 + net
                    daily.loc[dates[j], f"Q{g + 1}"] = ret
                    nav.loc[dates[j], f"Q{g + 1}"] = level[g]
                if j == i + 1:
                    ls_cost.loc[dates[j]] = (
                        (turnover[groups - 1] + turnover[0]) * cost_bps / 10000.0
                    )
            periods_net.append({f"Q{g + 1}": period_net[g] - 1 for g in range(groups)})
            periods_gross.append({f"Q{g + 1}": period_gross[g] - 1 for g in range(groups)})
            period_dates.append(dt)
        i += horizon
    turnover_df = pd.DataFrame(turnover_rows, index=period_dates)
    return QuantileResult(
        nav=nav.ffill(),
        turnover_df=turnover_df,
        periods_net=pd.DataFrame(periods_net, index=period_dates),
        periods_gross=pd.DataFrame(periods_gross, index=period_dates),
        daily=daily,
        ls_cost=ls_cost,
        turnover_mean=turnover_df.mean(),
    )
