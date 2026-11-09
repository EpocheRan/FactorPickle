"""因子研究报告的指标计算层。

口径约定（与回测一致）
---------------------
- **可交易口径**：T 日收盘出信号，**T+1 开盘价买入**，持有 h 日后收盘卖出；
  即 ``fwd_h = close[t+h] / open[t+1] - 1``。绝不用 close-to-close，
  否则反转/涨停类因子会被系统性高估。
- **IC**：逐日横截面 Spearman（秩相关），不做全样本统计。
- **t 值**：Newey-West 调整（lag = h-1）处理重叠样本；同时给出**非重叠**
  子样本 t 值（每 h 个交易日取一个观测），避免 NW 调整在小样本下过度自信。
- **中性化**：横截面回归剔除对数市值与行业哑变量后的残差 IC。
- **净值**：分组等权、非重叠调仓、T+1 开盘买入；成本按换手率×双边费率扣除。

本模块只做计算，不读数据库、不渲染。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np



import pandas as pd

from . import diagnostics as diag
from .data_quality import CleaningStandard, analyze as analyze_quality

TRADING_DAYS = 242


# ---------------------------------------------------------------- 基础工具
def _spearman_ic(factor_row: pd.Series, ret_row: pd.Series) -> float:
    pair = pd.concat([factor_row.rename("f"), ret_row.rename("r")], axis=1).dropna()
    if len(pair) < 30 or pair["f"].nunique() < 5 or pair["r"].nunique() < 5:
        return np.nan
    return float(pair["f"].rank().corr(pair["r"].rank()))


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


def forward_returns(
    close: pd.DataFrame, open_: pd.DataFrame, horizons: tuple[int, ...]
) -> dict[int, pd.DataFrame]:
    """T+1 开盘买入、T+h 收盘卖出的持有收益。"""
    entry = open_.shift(-1)
    return {h: close.shift(-h) / entry - 1 for h in horizons}


def newey_west_t(series: pd.Series, lag: int) -> float:
    clean = series.replace([np.inf, -np.inf], np.nan).dropna()
    n = len(clean)
    if n < 10:
        return np.nan
    values = clean.to_numpy(dtype="float64")
    mean = values.mean()
    demeaned = values - mean
    var = (demeaned ** 2).sum() / n
    for k in range(1, min(lag, n - 1) + 1):
        cov = (demeaned[k:] * demeaned[:-k]).sum() / n
        var += 2 * (1 - k / (lag + 1)) * cov
    if var <= 0:
        return np.nan
    return float(mean / np.sqrt(var / n))


def independent_t(series: pd.Series, step: int) -> tuple[float, int]:
    """非重叠子样本 t 值：每 ``step`` 个交易日取一个观测。

    重叠样本（h>1）的 NW-t 对滞后阶数敏感，且在小样本下容易高估显著性；
    报告同时给出独立子样本 t，作为「保守下界」。
    """
    clean = series.replace([np.inf, -np.inf], np.nan).dropna()
    if step < 1 or len(clean) < step * 10:
        return np.nan, 0
    sampled = clean.iloc[::step]
    n = len(sampled)
    std = sampled.std(ddof=1)
    if not np.isfinite(std) or std == 0:
        return np.nan, n
    return float(sampled.mean() / (std / np.sqrt(n))), n


def block_bootstrap_ci(
    series: pd.Series,
    *,
    block: int = 5,
    n_boot: int = 2000,
    alpha: float = 0.05,
    seed: int = 7,
) -> tuple[float, float]:
    """循环块自助法置信区间（保留重叠样本的自相关结构）。"""
    clean = series.replace([np.inf, -np.inf], np.nan).dropna()
    values = clean.to_numpy(dtype="float64")
    n = len(values)
    if n < max(30, block * 4):
        return np.nan, np.nan
    rng = np.random.default_rng(seed)
    n_blocks = int(np.ceil(n / block))
    starts = rng.integers(0, n, size=(n_boot, n_blocks))
    offsets = np.arange(block)
    idx = (starts[:, :, None] + offsets[None, None, :]) % n
    idx = idx.reshape(n_boot, -1)[:, :n]
    means = values[idx].mean(axis=1)
    return float(np.quantile(means, alpha / 2)), float(np.quantile(means, 1 - alpha / 2))


def _rank_ic_frame(
    factor: pd.DataFrame,
    fwd: pd.DataFrame,
    min_names: int = 20,
    *,
    factor_rank: pd.DataFrame | None = None,
) -> pd.Series:
    """逐日 Spearman 秩相关（全向量化）。

    ``factor_rank`` 可由调用方缓存；同一 signal 对 1/5/10/20 日只排一次。
    """
    common = factor.columns.intersection(fwd.columns)
    if len(common) == 0:
        return pd.Series(dtype="float64")
    a = (
        factor_rank[common].reindex(index=factor.index)
        if factor_rank is not None
        else factor[common].rank(axis=1)
    )
    b = fwd[common].reindex(index=factor.index).rank(axis=1)
    mask = a.notna() & b.notna()
    a = a.where(mask)
    b = b.where(mask)
    ma = a.mean(axis=1)
    mb = b.mean(axis=1)
    da = a.sub(ma, axis=0)
    db = b.sub(mb, axis=0)
    cov = (da * db).mean(axis=1)
    sa = da.pow(2).mean(axis=1).pow(0.5)
    sb = db.pow(2).mean(axis=1).pow(0.5)
    ic = cov / (sa * sb).replace(0, np.nan)
    ic = ic.where(mask.sum(axis=1) >= int(min_names))
    return ic.replace([np.inf, -np.inf], np.nan)


def _winsorize(values: np.ndarray, lower: float = 0.01, upper: float = 0.99) -> np.ndarray:
    finite = values[np.isfinite(values)]
    if len(finite) < 20:
        return values
    lo, hi = np.quantile(finite, [lower, upper])
    return np.clip(values, lo, hi)


def _winsorize_frame(frame: pd.DataFrame, lower: float = 0.01, upper: float = 0.99) -> pd.DataFrame:
    """逐日横截面缩尾；用于去极值敏感性对照（不改变秩 IC）。"""
    arr = frame.to_numpy(dtype="float64")
    out = arr.copy()
    for i in range(arr.shape[0]):
        row = arr[i]
        finite = np.isfinite(row)
        if finite.sum() < 20:
            continue
        lo, hi = np.quantile(row[finite], [lower, upper])
        out[i, finite] = np.clip(row[finite], lo, hi)
    return pd.DataFrame(out, index=frame.index, columns=frame.columns)


def _winsor_group_returns(
    factor: pd.DataFrame, forward: pd.DataFrame, *, groups: int = 5,
    horizon: int = 5, lower: float = 0.01, upper: float = 0.99,
) -> dict:
    """组内收益缩尾后的分组均值：回答“G5 断崖是不是极端收益拉动”。"""
    buckets: dict[int, list[float]] = {g: [] for g in range(groups)}
    i = 0
    while i + horizon + 1 < len(factor.index):
        row = factor.iloc[i]
        ret = forward.iloc[i]
        valid = row.notna() & ret.notna()
        if int(valid.sum()) >= groups * 5:
            ranked = row[valid].rank(method="first")
            q = pd.qcut(ranked, groups, labels=False, duplicates="drop")
            for g in range(groups):
                vals = ret[valid][q == g].dropna()
                if len(vals) < 5:
                    continue
                lo, hi = np.quantile(vals, [lower, upper])
                buckets[g].append(float(np.clip(vals, lo, hi).mean()))
        i += horizon
    means = {f"Q{g + 1}": float(np.mean(v)) for g, v in buckets.items() if v}
    if "Q1" in means and f"Q{groups}" in means:
        means["spread"] = means[f"Q{groups}"] - means["Q1"]
    return means


def _zscore(values: np.ndarray) -> np.ndarray:
    finite = np.isfinite(values)
    if finite.sum() < 2:
        return values
    mean = np.nanmean(values[finite])
    std = np.nanstd(values[finite])
    if not np.isfinite(std) or std == 0:
        return values
    out = (values - mean) / std
    out[~finite] = np.nan
    return out


def neutralize_panel(
    factor: pd.DataFrame,
    market_cap: pd.DataFrame | None,
    industry: pd.DataFrame | None,
    *,
    mode: str = "both",
) -> pd.DataFrame:
    """横截面中性化：对 log 市值与行业哑变量回归，返回残差面板。

    ``mode``：``size`` / ``industry`` / ``both``。
    行业用**当日**的静态映射（见报告局限）；先缩尾再标准化，避免极值主导回归。
    """
    values = factor.to_numpy(dtype="float64")
    n_rows, n_cols = values.shape
    cap = (
        market_cap.reindex(index=factor.index, columns=factor.columns).to_numpy(dtype="float64")
        if market_cap is not None
        else np.full_like(values, np.nan)
    )
    with np.errstate(divide="ignore", invalid="ignore"):
        log_cap = np.where(cap > 0, np.log(cap), np.nan)

    codes = np.full(n_cols, -1, dtype="int64")
    if industry is not None and mode in ("industry", "both"):
        if isinstance(industry, pd.Series):
            # Series(代码→行业)：直接按因子列对齐，无需 ffill
            last = industry.reindex(factor.columns)
        else:
            last = industry.reindex(columns=factor.columns).ffill().iloc[-1]
        mapping = {name: i for i, name in enumerate(pd.unique(last.dropna()))}
        codes = np.array([mapping.get(v, -1) if pd.notna(v) else -1 for v in last.to_numpy()], dtype="int64")

    out = np.full_like(values, np.nan)
    for i in range(n_rows):
        y = _winsorize(values[i])
        valid = np.isfinite(y)
        if valid.sum() < 30:
            continue
        y = _zscore(y)
        resid = y.copy()

        if mode in ("industry", "both") and codes.max() >= 0:
            group = codes
            valid_g = valid & (group >= 0)
            if valid_g.sum() >= 30:
                counts = np.bincount(group[valid_g], minlength=int(group.max()) + 1)
                sums = np.bincount(group[valid_g], weights=y[valid_g], minlength=len(counts))
                means = np.divide(sums, counts, out=np.zeros_like(sums), where=counts > 0)
                resid[valid_g] = y[valid_g] - means[group[valid_g]]

        if mode in ("size", "both"):
            cap_row = _zscore(log_cap[i])
            both = np.isfinite(resid) & np.isfinite(cap_row)
            if both.sum() >= 30:
                x = cap_row[both]
                r = resid[both]
                # 行业中性化后，cap 也要在行业内去均值，否则行业与市值共线
                if mode == "both" and codes.max() >= 0:
                    group = codes
                    ok_g = both & (group >= 0)
                    if ok_g.sum() >= 30:
                        counts = np.bincount(group[ok_g], minlength=int(group.max()) + 1)
                        sums = np.bincount(
                            group[ok_g], weights=cap_row[ok_g], minlength=len(counts)
                        )
                        means = np.divide(sums, counts, out=np.zeros_like(sums), where=counts > 0)
                        cap_neutral = cap_row.copy()
                        cap_neutral[ok_g] = cap_row[ok_g] - means[group[ok_g]]
                        x = cap_neutral[both]
                denom = float((x * x).sum())
                if denom > 0:
                    beta = float((r * x).sum() / denom)
                    resid[both] = r - beta * x
        out[i] = resid
    return pd.DataFrame(out, index=factor.index, columns=factor.columns)


def neutralized_ic(
    factor: pd.DataFrame,
    forward: pd.DataFrame,
    market_cap: pd.DataFrame | None,
    industry: pd.DataFrame | None,
) -> pd.DataFrame:
    """原始 / 市值中性 / 行业中性 / 双中性 的 IC 对比。"""
    rows = {}
    variants = {
        "原始": None,
        "市值中性": "size" if market_cap is not None else None,
        "行业中性": "industry" if industry is not None else None,
        "双中性": "both" if (market_cap is not None and industry is not None) else None,
    }
    for label, mode in variants.items():
        if label != "原始" and mode is None:
            continue
        panel = factor if mode is None else neutralize_panel(factor, market_cap, industry, mode=mode)
        series = _rank_ic_frame(panel, forward)
        if series.empty:
            continue
        rows[label] = {
            "mean": float(series.mean()),
            "std": float(series.std()),
            "ir": float(series.mean() / series.std() * np.sqrt(TRADING_DAYS / 5)) if series.std() else np.nan,
            "t": newey_west_t(series, lag=4),
            "n": int(len(series)),
        }
    return pd.DataFrame(rows).T


# ---------------------------------------------------------------- 组合净值
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
    from .metrics_backend import resolve_quantile_backend

    if resolve_quantile_backend(int(factor.shape[0]) * int(factor.shape[1])) == "polars":
        from .metrics_polars import quantile_nav as _polars_quantile_nav

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


def _performance(nav: pd.Series) -> dict:
    clean = nav.dropna()
    if len(clean) < 2:
        return {}
    daily = clean.pct_change().dropna()
    total = float(clean.iloc[-1] / clean.iloc[0] - 1)
    years = len(clean) / TRADING_DAYS
    ann = float((1 + total) ** (1 / years) - 1) if years > 0 and total > -1 else np.nan
    vol = float(daily.std() * np.sqrt(TRADING_DAYS))
    sharpe = float(daily.mean() / daily.std() * np.sqrt(TRADING_DAYS)) if daily.std() else np.nan
    drawdown = clean / clean.cummax() - 1
    mdd = float(drawdown.min())
    calmar = float(ann / abs(mdd)) if mdd < 0 and np.isfinite(ann) else np.nan
    return {
        "total": total,
        "ann": ann,
        "vol": vol,
        "sharpe": sharpe,
        "mdd": mdd,
        "calmar": calmar,
        "win": float((daily > 0).mean()),
        "n_days": int(len(clean)),
    }


def _period_performance(period_returns: pd.Series, horizon: int) -> dict:
    """非重叠持有期组合的绩效（与分层表同一策略）。"""
    clean = period_returns.dropna()
    if len(clean) < 2:
        return {}
    nav = (1 + clean).cumprod()
    total = float(nav.iloc[-1] - 1)
    ann = float(nav.iloc[-1] ** (TRADING_DAYS / (len(clean) * horizon)) - 1)
    vol = float(clean.std() * np.sqrt(TRADING_DAYS / horizon))
    sharpe = float(clean.mean() / clean.std() * np.sqrt(TRADING_DAYS / horizon)) if clean.std() else np.nan
    drawdown = nav / nav.cummax() - 1
    mdd = float(drawdown.min())
    calmar = float(ann / abs(mdd)) if mdd < 0 else np.nan
    return {
        "total": total,
        "ann": ann,
        "vol": vol,
        "sharpe": sharpe,
        "mdd": mdd,
        "calmar": calmar,
        "win": float((clean > 0).mean()),
        "n_days": int(len(clean)),
    }


def _annual_returns(nav: pd.Series) -> pd.Series:
    clean = nav.dropna()
    if clean.empty:
        return pd.Series(dtype="float64")
    yearly = clean.groupby(clean.index.year).last()
    prev = yearly.shift(1)
    first = clean.iloc[0]
    out = {}
    for year, value in yearly.items():
        base = prev.get(year, np.nan)
        if not np.isfinite(base):
            base = first if year == yearly.index[0] else np.nan
        out[int(year)] = float(value / base - 1) if base and np.isfinite(base) else np.nan
    return pd.Series(out)


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


# ---------------------------------------------------------------- 报告数据
@dataclass
class FactorReport:
    factor_id: str
    category: str
    direction: int
    universe: str
    start: pd.Timestamp
    end: pd.Timestamp
    n_symbols: int
    n_dates: int
    coverage: float
    horizons: tuple[int, ...]

    ic: dict[int, pd.Series] = field(default_factory=dict)
    ic_stats: pd.DataFrame = field(default_factory=pd.DataFrame)
    rolling_ic: pd.Series | None = None
    ic_by_year: pd.Series | None = None
    ic_by_year_ci: pd.DataFrame | None = None
    ic_by_month: pd.Series | None = None
    ic_by_month_ci: pd.DataFrame | None = None
    ic_by_regime: pd.Series | None = None
    regime_stats: pd.DataFrame | None = None
    regime_stats_alt: pd.DataFrame | None = None
    regime_share: pd.DataFrame | None = None
    regime_corr: float = float("nan")
    regime_corr_p: float = float("nan")
    decay: pd.Series | None = None
    decay_note: str = ""

    nav: pd.DataFrame | None = None
    period_returns: pd.DataFrame | None = None
    period_returns_gross: pd.DataFrame | None = None
    period_returns_net: pd.DataFrame | None = None
    daily_returns: pd.DataFrame | None = None
    winsor: dict = field(default_factory=dict)
    return_winsor: dict = field(default_factory=dict)
    tail_specificity: dict = field(default_factory=dict)
    quantile_returns: dict[str, float] = field(default_factory=dict)
    quantile_returns_gross: dict[str, float] = field(default_factory=dict)
    quantile_ci: dict[str, tuple[float, float]] = field(default_factory=dict)
    monotonicity: float = float("nan")
    monotonicity_net: float = float("nan")
    monotonicity_p: float = float("nan")
    spread_gross: dict = field(default_factory=dict)
    spread_net: dict = field(default_factory=dict)
    cost_mean: float = float("nan")
    sigma_5d: float = float("nan")
    transmission: dict = field(default_factory=dict)
    diagnostics: list = field(default_factory=list)
    attribution: dict = field(default_factory=dict)
    direction_source: str = "registry"
    ic_half: dict = field(default_factory=dict)
    reproduction: dict = field(default_factory=dict)
    long_short: dict[str, float] = field(default_factory=dict)
    ls_nav: pd.Series | None = None
    ls_daily: pd.Series | None = None
    drawdown: pd.Series | None = None
    perf: dict = field(default_factory=dict)
    annual_returns: pd.Series | None = None
    benchmark_nav: pd.Series | None = None
    turnover: float = float("nan")
    turnover_by_group: pd.Series | None = None

    filter_test: dict = field(default_factory=dict)
    filter_test_opposite: dict = field(default_factory=dict)
    strat_ic: object | None = None
    size_ranges: dict | None = None
    horizon_spreads: object | None = None
    annual_ic_return: pd.DataFrame | None = None
    metrics_engine: dict[str, str] = field(default_factory=dict)

    ic_neutral: pd.DataFrame | None = None
    ic_by_size: pd.Series | None = None
    ic_by_industry: pd.Series | None = None
    industry_neutral_ic: float = float("nan")
    autocorr: float = float("nan")

    limit_up: dict = field(default_factory=dict)
    tradeability: dict = field(default_factory=dict)
    sell_side: dict = field(default_factory=dict)
    cost_table: pd.DataFrame | None = None
    capacity: dict = field(default_factory=dict)
    incrementality: dict = field(default_factory=dict)
    peers: pd.Series | None = None

    definition: dict = field(default_factory=dict)
    factor_stats: dict = field(default_factory=dict)
    data_quality: dict = field(default_factory=dict)
    pool: dict = field(default_factory=dict)
    pool_robustness: pd.DataFrame | None = None
    sensitivity: list[dict] = field(default_factory=list)
    freshness: dict = field(default_factory=dict)
    raw_direction: int = 1
    flipped: bool = False
    rating: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def _regime_table(ic: pd.Series, regime: pd.Series, *, block: int = 5) -> pd.DataFrame:
    rows = {}
    aligned = regime.reindex(ic.index)
    for key, label in (("bull", "牛市"), ("neutral", "震荡"), ("bear", "熊市")):
        sub = ic[aligned == key].dropna()
        if len(sub) < 20:
            continue
        lo, hi = block_bootstrap_ci(sub, block=block)
        rows[label] = {
            "mean": float(sub.mean()),
            "n": int(len(sub)),
            "ci_lo": lo,
            "ci_hi": hi,
            "t": newey_west_t(sub, lag=4),
        }
    if {"牛市", "熊市"}.issubset(rows):
        bull = ic[aligned == "bull"].dropna()
        bear = ic[aligned == "bear"].dropna()
        diff = float(bull.mean() - bear.mean())
        rng = np.random.default_rng(11)
        boot = []
        for _ in range(2000):
            b = bull.to_numpy()[rng.integers(0, len(bull), len(bull))].mean()
            r = bear.to_numpy()[rng.integers(0, len(bear), len(bear))].mean()
            boot.append(b - r)
        boot = np.asarray(boot)
        rows["牛市−熊市"] = {
            "mean": diff,
            "n": int(len(bull) + len(bear)),
            "ci_lo": float(np.quantile(boot, 0.025)),
            "ci_hi": float(np.quantile(boot, 0.975)),
            "t": float("nan"),
            "p": float(2 * min((boot <= 0).mean(), (boot >= 0).mean())),
        }
    return pd.DataFrame(rows).T


def _decay_note(decay: pd.Series) -> str:
    clean = decay.dropna()
    if len(clean) < 2:
        return "持有期样本不足，无法判断衰减形态。"
    hs = list(clean.index)
    vals = clean.to_numpy()
    abs_vals = np.abs(vals)
    peak_pos = int(np.argmax(abs_vals))
    peak_h = hs[peak_pos]
    peak = float(vals[peak_pos])
    peak_abs = float(abs_vals[peak_pos])
    first_abs = float(abs_vals[0])
    if peak_abs < 0.005:
        return "各持有期 IC 均接近 0，衰减形态无经济含义。"
    if peak_h == max(hs) and peak_abs > first_abs * 1.2:
        return (
            f"IC 绝对值随持有期增强，在 h={peak_h} 达到 |IC|={peak_abs:.3f}"
            f"（IC={peak:+.3f}），未见衰减；5 日并非最优持有期，"
            "延长持有期可提高信号强度，但需同时评估样本重叠、容量与调仓成本。"
        )
    if peak_h == min(hs) and peak_abs > abs_vals[-1] * 1.2:
        return (
            f"IC 绝对值在 h={peak_h} 最大（|IC|={peak_abs:.3f}，IC={peak:+.3f}），"
            "随后走弱；信号主要集中在短持有期。"
        )
    return (
        f"IC 绝对值在 h={peak_h} 最大（|IC|={peak_abs:.3f}，IC={peak:+.3f}），"
        "随后走弱；衰减速度见曲线。"
    )


def _capacity(amount: pd.DataFrame, factor: pd.DataFrame, horizon: int, groups: int) -> dict:
    """容量估算：Top 组等权，单票不超过 ADV 的 10%，3 日建仓。"""
    if amount is None or amount.empty or factor.empty:
        return {}
    dates = factor.index
    adv = amount.rolling(20, min_periods=5).mean()
    caps = []
    last_rows = []
    i = 0
    while i + horizon + 1 < len(dates):
        dt = dates[i]
        row = factor.loc[dt].dropna()
        if len(row) >= groups * 5:
            ranked = row.rank(method="first")
            buckets = pd.qcut(ranked, groups, labels=False, duplicates="drop")
            members = list(row.index[buckets == groups - 1])
            if members and dt in adv.index:
                values = adv.loc[dt, members].dropna()
                if len(values) >= max(5, len(members) // 2):
                    per_name = values * 0.1 * 3  # 10% ADV × 3 天
                    caps.append(float(np.quantile(per_name, 0.1) * len(members)))
                    last_rows.append(values)
        i += horizon
    if not caps:
        return {}
    latest = last_rows[-1] if last_rows else pd.Series(dtype="float64")
    return {
        "aum_est": float(np.median(caps)),
        "aum_est_min": float(np.min(caps)),
        "aum_est_max": float(np.max(caps)),
        "n_rebalance": len(caps),
        "median_adv": float(latest.median()) if not latest.empty else np.nan,
        "min_adv": float(latest.min()) if not latest.empty else np.nan,
        "assumption": "单票不超过 20 日均额的 10%，3 个交易日建仓；容量按成分股 10% 分位约束",
    }


def _filter_test(period_returns: pd.DataFrame, horizon: int) -> dict:
    """过滤价值检验：剔除 Q1 后组合是否真的变好。

    过滤的严格定义不是「Q1 最差」，而是「剔除 Q1 后夏普/回撤/尾部风险改善」。
    如果 Q1 的绝对收益与全组均值接近、剔除后夏普不升，则过滤只是放弃收益。
    """
    if period_returns is None or period_returns.empty or "Q1" not in period_returns:
        return {}
    cols = list(period_returns.columns)
    q1 = period_returns["Q1"].dropna()
    rest_cols = [c for c in cols if c != "Q1"]
    rest = period_returns[rest_cols].mean(axis=1).dropna()
    all_long = period_returns.mean(axis=1).dropna()
    if len(q1) < 20:
        return {}

    def _perf(series: pd.Series) -> dict:
        nav = (1 + series).cumprod()
        ann = float(nav.iloc[-1] ** (TRADING_DAYS / (len(series) * horizon)) - 1)
        vol = float(series.std() * np.sqrt(TRADING_DAYS / horizon))
        sharpe = float(series.mean() / series.std() * np.sqrt(TRADING_DAYS / horizon)) if series.std() else np.nan
        mdd = float((nav / nav.cummax() - 1).min())
        return {"ann": ann, "vol": vol, "sharpe": sharpe, "mdd": mdd}

    q1_perf, rest_perf, all_perf = _perf(q1), _perf(rest), _perf(all_long)
    spread = period_returns["Q5"] - period_returns["Q1"] if "Q5" in period_returns else pd.Series(dtype=float)
    spread = spread.dropna()
    se = float(spread.std(ddof=1) / np.sqrt(len(spread))) if len(spread) > 2 else np.nan
    rel = ((1 + q1).cumprod() / (1 + all_long).cumprod()).dropna()
    rel_mdd = float((rel / rel.cummax() - 1).min()) if len(rel) else np.nan
    tail_q1 = float((q1 < -0.03).mean())
    tail_rest = float((rest < -0.03).mean())
    ann_factor = TRADING_DAYS / horizon
    q1_excess = float(q1.mean() - all_long.mean())
    useful = bool(
        rest_perf["sharpe"] > all_perf["sharpe"] + 0.05
        and q1.mean() < all_long.mean() - 0.001
        and tail_q1 > tail_rest + 0.03
    )
    return {
        "q1_mean": float(q1.mean()),
        "rest_mean": float(rest.mean()),
        "all_mean": float(all_long.mean()),
        "q1_ann": float(q1.mean() * ann_factor),
        "rest_ann": float(rest.mean() * ann_factor),
        "all_ann": float(all_long.mean() * ann_factor),
        "q1_cagr": q1_perf["ann"],
        "rest_cagr": rest_perf["ann"],
        "all_cagr": all_perf["ann"],
        "q1_excess": q1_excess,
        "q1_excess_ann": float(q1_excess * ann_factor),
        "q1_sharpe": q1_perf["sharpe"],
        "rest_sharpe": rest_perf["sharpe"],
        "all_sharpe": all_perf["sharpe"],
        "q1_mdd": q1_perf["mdd"],
        "rel_mdd": rel_mdd,
        "tail_q1": tail_q1,
        "tail_rest": tail_rest,
        "spread_mean": float(spread.mean()) if len(spread) else np.nan,
        "spread_se": se,
        "useful": useful,
    }


def _filter_test_side(period_returns: pd.DataFrame, horizon: int, drop_group: str) -> dict:
    """通用单侧过滤检验：剔除指定组后，组合是否改善。

    与 ``_filter_test`` 的区别是组名可配置；用于同时检查 Q1 和 Q5，
    避免只因为 direction=+1 就只测“最弱组”。
    """
    if period_returns is None or period_returns.empty or drop_group not in period_returns:
        return {}
    cols = list(period_returns.columns)
    dropped = period_returns[drop_group].dropna()
    rest_cols = [c for c in cols if c != drop_group]
    rest = period_returns[rest_cols].mean(axis=1).dropna()
    all_long = period_returns.mean(axis=1).dropna()
    if len(dropped) < 20:
        return {}

    def _perf(series: pd.Series) -> dict:
        nav = (1 + series).cumprod()
        ann = float(nav.iloc[-1] ** (TRADING_DAYS / (len(series) * horizon)) - 1)
        vol = float(series.std() * np.sqrt(TRADING_DAYS / horizon))
        sharpe = float(series.mean() / series.std() * np.sqrt(TRADING_DAYS / horizon)) if series.std() else np.nan
        mdd = float((nav / nav.cummax() - 1).min())
        return {"ann": ann, "vol": vol, "sharpe": sharpe, "mdd": mdd}

    d_perf, r_perf, a_perf = _perf(dropped), _perf(rest), _perf(all_long)
    rel = ((1 + dropped).cumprod() / (1 + all_long).cumprod()).dropna()
    rel_mdd = float((rel / rel.cummax() - 1).min()) if len(rel) else np.nan
    tail_d = float((dropped < -0.03).mean())
    tail_r = float((rest < -0.03).mean())
    diff = (rest - all_long).dropna()
    diff_se = float(diff.std(ddof=1) / np.sqrt(len(diff))) if len(diff) > 2 else np.nan
    diff_t = float(diff.mean() / diff_se) if diff_se and np.isfinite(diff_se) else np.nan
    diff_ci = (
        (float(diff.mean() - 1.96 * diff_se), float(diff.mean() + 1.96 * diff_se))
        if np.isfinite(diff_se) else (np.nan, np.nan)
    )
    # 四项改善里至少 3 项，避免单一阈值（例如尾部差 2.6pp）把明显改善判死。
    improvements = [
        bool(r_perf["sharpe"] > a_perf["sharpe"] + 0.05),
        bool(dropped.mean() < all_long.mean() - 0.001),
        bool(r_perf["mdd"] > a_perf["mdd"]),
        bool(tail_d > tail_r + 0.01),
    ]
    useful = bool(sum(improvements) >= 3)
    ann_factor = TRADING_DAYS / horizon
    return {
        "drop_group": drop_group,
        "dropped_mean": float(dropped.mean()),
        "rest_mean": float(rest.mean()),
        "all_mean": float(all_long.mean()),
        "dropped_ann": float(dropped.mean() * ann_factor),
        "rest_ann": float(rest.mean() * ann_factor),
        "all_ann": float(all_long.mean() * ann_factor),
        "dropped_sharpe": d_perf["sharpe"],
        "rest_sharpe": r_perf["sharpe"],
        "all_sharpe": a_perf["sharpe"],
        "dropped_mdd": d_perf["mdd"],
        "rest_mdd": r_perf["mdd"],
        "all_mdd": a_perf["mdd"],
        "rel_mdd": rel_mdd,
        "tail_dropped": tail_d,
        "tail_rest": tail_r,
        "excess_mean": float(diff.mean()) if len(diff) else np.nan,
        "excess_t": diff_t,
        "excess_ci": diff_ci,
        "improvements": improvements,
        "useful": useful,
    }


def _annual_ic_return(ic_by_year: pd.Series | None, annual: pd.Series | None) -> pd.DataFrame | None:
    if ic_by_year is None or annual is None:
        return None
    frame = pd.concat([ic_by_year.rename("ic"), annual.rename("ret")], axis=1).dropna()
    if frame.empty:
        return None
    return frame


def _corr_p(r: float, n: int) -> float:
    """Fisher z 近似的相关系数双尾 p 值（不依赖 scipy）。"""
    if not np.isfinite(r) or n < 4:
        return float("nan")
    from math import atanh, erfc, sqrt

    z = atanh(max(-0.999999, min(0.999999, r))) * sqrt(n - 3)
    return float(erfc(abs(z) / sqrt(2)))


def _incremental(
    factor: pd.DataFrame,
    forward: pd.DataFrame,
    peers: dict[str, pd.DataFrame] | None,
) -> tuple[dict, pd.Series | None]:
    """增量性：与同类因子的相关性 + 剔除同类后的残差 IC。"""
    if not peers:
        return {}, None
    dates = factor.index
    sample = dates[:: max(1, len(dates) // 120)]
    corrs = {}
    for name, panel in peers.items():
        if panel is None or panel.empty:
            continue
        values = []
        for dt in sample:
            if dt not in panel.index or dt not in factor.index:
                continue
            a = factor.loc[dt]
            b = panel.loc[dt]
            pair = pd.concat([a.rename("a"), b.rename("b")], axis=1).dropna()
            if len(pair) >= 30 and pair["a"].nunique() > 5 and pair["b"].nunique() > 5:
                values.append(pair["a"].rank().corr(pair["b"].rank()))
        if values:
            corrs[name] = float(np.nanmean(values))
    if not corrs:
        return {}, None
    corr_series = pd.Series(corrs).sort_values(key=lambda s: s.abs(), ascending=False)

    # 残差 IC：每个截面把因子对全部同类因子回归，取残差
    peer_names = list(corr_series.index)
    residual = factor.copy()
    values = residual.to_numpy(dtype="float64")
    peer_values = [peers[n].reindex_like(factor).to_numpy(dtype="float64") for n in peer_names]
    for i in range(len(dates)):
        y = values[i]
        cols = [p[i] for p in peer_values]
        valid = np.isfinite(y)
        for c in cols:
            valid &= np.isfinite(c)
        if valid.sum() < 40:
            continue
        x = np.column_stack([np.ones(valid.sum())] + [c[valid] for c in cols])
        try:
            beta, *_ = np.linalg.lstsq(x, y[valid], rcond=None)
        except np.linalg.LinAlgError:
            continue
        resid = y[valid] - x @ beta
        values[i, valid] = resid
    residual = pd.DataFrame(values, index=factor.index, columns=factor.columns)
    ic_raw = float(_rank_ic_frame(factor, forward).mean())
    ic_resid = float(_rank_ic_frame(residual, forward).mean())
    raw_ratio = ic_resid / ic_raw if np.isfinite(ic_raw) and ic_raw != 0 else np.nan
    max_corr = float(corr_series.abs().max())
    # 只有「原始 IC 为正 且 残差 IC 为正」时，保留率才有经济含义。
    # 负/负 会得到 +270% 这种假高分；原始方向无效时增量性直接 0。
    effective = raw_ratio if (np.isfinite(raw_ratio) and ic_raw > 0 and ic_resid > 0) else 0.0
    base = float(np.clip(effective, 0, 1) * 10)
    diversity = float(np.clip((0.90 - max_corr) / 0.40, 0, 1) * 5)
    # 残差为负或与同类几乎相同：不是「保留率低」，而是重复因子，增量性应为 0
    if (np.isfinite(raw_ratio) and raw_ratio < 0) or max_corr >= 0.95 or ic_raw <= 0:
        score = 0.0
    else:
        score = float(np.clip(base + diversity, 0, 15))
    return {
        "peers": corr_series,
        "max_corr": max_corr,
        "ic_raw": ic_raw,
        "ic_resid": ic_resid,
        "ratio": raw_ratio,
        "effective_ratio": effective,
        "score": score,
        "residual": residual,
    }, corr_series


def neutralize_factor(factor: pd.DataFrame, market_cap: pd.DataFrame | None) -> pd.DataFrame:
    """逐日对 log 市值做横截面回归取残差（市值中性化），返回同形状因子。"""
    if market_cap is None:
        return factor
    mc = market_cap.reindex(index=factor.index, columns=factor.columns)
    logmc = np.log(np.where(mc > 0, mc, np.nan))
    out = pd.DataFrame(index=factor.index, columns=factor.columns, dtype="float64")
    for dt in factor.index:
        y = factor.loc[dt].to_numpy(dtype="float64")
        x = logmc[factor.index.get_loc(dt)] if isinstance(logmc, np.ndarray) else logmc.loc[dt].to_numpy(dtype="float64")
        mask = np.isfinite(y) & np.isfinite(x)
        if mask.sum() < 30:
            continue
        xx = x[mask]
        yy = y[mask]
        xx = xx - xx.mean()
        beta = float((xx * (yy - yy.mean())).sum() / max((xx * xx).sum(), 1e-12))
        resid = yy - yy.mean() - beta * xx
        out.loc[dt, np.array(factor.columns)[mask]] = resid
    return out


def _as_industry_frame(industry, like: pd.DataFrame):
    """统一 industry 入参类型：Series(代码→行业) 广播成与因子同形状的宽表。"""
    if industry is None or not isinstance(industry, pd.Series):
        return industry
    cols = like.columns
    mapping = industry.reindex(cols).to_numpy()
    return pd.DataFrame(np.tile(mapping, (len(like), 1)), index=like.index, columns=cols)


def _stratified_ic(factor, fwd, strata) -> pd.DataFrame:
    """按分层掩码计算 IC 均值 / 独立 t / 样本日数（numpy 单遍实现）。

    性能要点：掩码与数值只转一次 numpy，逐日循环里用布尔索引切片，
    避免每层都构造一次 1,483×5,863 的 mask DataFrame（原实现的真正瓶颈）。
    """
    if isinstance(factor, pd.DataFrame):
        x_all = factor.to_numpy(dtype="float64")
        y_all = fwd.reindex(index=factor.index, columns=factor.columns).to_numpy(dtype="float64")
        index = factor.index
    else:
        x_all, y_all, index = factor
    out = []
    for name, mask in strata.items():
        mask_np = mask.to_numpy() if isinstance(mask, pd.DataFrame) else np.asarray(mask)
        ics = np.full(len(index), np.nan)
        for i in range(len(index)):
            m = mask_np[i]
            x = x_all[i]
            y = y_all[i]
            sel = m & np.isfinite(x) & np.isfinite(y)
            k = int(sel.sum())
            if k < 20:
                continue
            xs = x[sel]
            ys = y[sel]
            rx = pd.Series(xs).rank().to_numpy()
            ry = pd.Series(ys).rank().to_numpy()
            rx -= rx.mean()
            ry -= ry.mean()
            den = np.sqrt((rx * rx).sum() * (ry * ry).sum())
            if den > 0:
                ics[i] = float((rx * ry).sum() / den)
        series = pd.Series(ics).replace([np.inf, -np.inf], np.nan).dropna()
        if len(series) < 20:
            out.append({"stratum": name, "ic": np.nan, "t": np.nan, "n_days": len(series)})
            continue
        se = series.std(ddof=1) / np.sqrt(len(series))
        out.append({"stratum": name, "ic": float(series.mean()),
                    "t": float(series.mean() / se) if se > 0 else np.nan,
                    "n_days": int(len(series))})
    return pd.DataFrame(out)


def build_report(
    factor: pd.DataFrame,
    close: pd.DataFrame,
    open_: pd.DataFrame,
    *,
    factor_id: str,
    category: str = "momentum",
    direction: int = 1,
    universe: str = "csi800",
    horizons: tuple[int, ...] = (1, 5, 10, 20, 60),
    primary_horizon: int = 5,
    groups: int = 5,
    cost_bps: float = 20.0,
    regime: pd.Series | None = None,
    regime_alt: pd.Series | None = None,
    market_cap: pd.DataFrame | None = None,
    industry: pd.DataFrame | None = None,
    amount: pd.DataFrame | None = None,
    low: pd.DataFrame | None = None,
    cleaning: CleaningStandard | None = None,
    benchmark: pd.Series | None = None,
    peers: dict[str, pd.DataFrame] | None = None,
    definition: dict | None = None,
    pool: dict | None = None,
    neutralize: bool = False,
    freshness: dict | None = None,
    is_st: dict[str, bool] | None = None,
    exclude_limit_up: bool = False,
    direction_source: str = "registry",
    full: bool = True,
    sections: tuple[str, ...] | None = None,
) -> FactorReport:
    sections = tuple(sections or ("definition", "timing", "cross_section", "rating", "appendix"))
    need_cross_extra = "cross_section" in sections
    need_rating = "rating" in sections
    need_cross_core = need_cross_extra or need_rating
    factor = factor.sort_index()
    # 因子面板可能比行情短（预热期被裁掉）。统一裁到因子日期，避免
    # 可成交分母、成本、容量把预热期算进去，也避免 fwd 跨到评估起点之前。
    if not factor.index.equals(close.index):
        close = close.reindex(factor.index)
        open_ = open_.reindex(factor.index)
    # 有效起点：横截面至少 20%（且不少于 30 只）有因子值。财务 value 因子
    # 2023 才有数据，但面板从 2019 开始；不裁会让 report.start/n_dates/覆盖率
    # 都错报成 2019 起、44% 覆盖，并误触发覆盖率否决。
    counts = factor.notna().sum(axis=1)
    min_names = max(30, int(0.2 * factor.shape[1]))
    usable = counts[counts >= min_names]
    if len(usable):
        effective_start = usable.index.min()
        factor = factor.loc[factor.index >= effective_start]
        close = close.loc[close.index >= effective_start]
        open_ = open_.loc[open_.index >= effective_start]
    fwd = forward_returns(close, open_, horizons)
    mask = tradeable_mask(close, open_, is_st=is_st)

    report = FactorReport(
        factor_id=factor_id,
        category=category,
        direction=direction,
        universe=universe,
        start=factor.index.min(),
        end=factor.index.max(),
        n_symbols=int(factor.shape[1]),
        n_dates=int(factor.shape[0]),
        coverage=float(factor.notna().sum().sum() / max(1, factor.size)),
        horizons=horizons,
        definition=definition or {},
        pool=pool or {},
        freshness=freshness or {},
        raw_direction=direction,
    )
    from .metrics_backend import engine_metadata

    report.metrics_engine = engine_metadata(factor.shape)

    quality = None
    if cleaning is not None:
        quality = analyze_quality(
            factor, close, open_, low=low, amount=amount,
            standard=cleaning, horizon=primary_horizon,
        )
        report.data_quality = quality.summary
        report.n_symbols = int(quality.summary["universe_symbols"])
        report.coverage = float(
            quality.summary["coverage"].get("valid_signal_ratio",
                                            quality.summary["coverage"]["valid_signal"])
        )
        report.universe = f"{universe} · {cleaning.label}"

    # ---- 方向：唯一入口。
    # 因子值本身不取负；分析信号 = raw × direction。**不做样本内翻转**：
    # 在同一样本上按 IC 符号选方向，等价于用未来信息择向，会把随机因子的 IC
    # 系统性抬成正数（旧版 4/4 强因子 IC>0、ρ 与价差打架，根因就在这里）。
    if direction not in (-1, 1):
        raise ValueError(f"direction 必须是 +1/-1，收到 {direction!r}")
    industry = _as_industry_frame(industry, factor)
    analysis_factor = factor * int(direction)
    if quality is not None:
        analysis_factor = analysis_factor.where(quality.mask)
    if neutralize:
        analysis_factor = neutralize_factor(analysis_factor, market_cap)
        report.neutralized = True
    report.direction = int(direction)
    report.raw_direction = int(direction)
    report.direction_source = direction_source
    report.flipped = False
    factor_rank = analysis_factor.rank(axis=1)

    valid_values = factor.to_numpy(dtype="float64")
    finite = valid_values[np.isfinite(valid_values)]
    if len(finite):
        report.factor_stats = {
            "median": float(np.median(finite)),
            "p01": float(np.quantile(finite, 0.01)),
            "p99": float(np.quantile(finite, 0.99)),
            "skew": float(pd.Series(finite).skew()),
            "min": float(finite.min()),
            "max": float(finite.max()),
            "missing": float(1 - len(finite) / valid_values.size),
            "n": int(len(finite)),
        }
    # 可成交过滤：t+1 开盘一字涨停/停牌的样本不可买入，IC 与分层都必须剔除
    fwd_raw = dict(fwd)
    if exclude_limit_up:
        fwd = {h: v.where(mask) for h, v in fwd.items()}
    report.limit_up = {
        "applied": bool(exclude_limit_up),
        "excluded_ratio": float((~mask).to_numpy().mean()),
        "ic_raw": float(_rank_ic_frame(analysis_factor, fwd_raw[primary_horizon],
                                       factor_rank=factor_rank).mean()),
        "ic_masked": float(_rank_ic_frame(analysis_factor, fwd[primary_horizon],
                                          factor_rank=factor_rank).mean()),
    }

    # 横截面收益离散度：IC 传导到 Q5−Q1 的分母（报告里不再写死 7%）
    sigma_series = fwd[primary_horizon].std(axis=1, skipna=True)
    sigma_series = sigma_series[fwd[primary_horizon].notna().sum(axis=1) >= 30]
    report.sigma_5d = float(sigma_series.mean()) if len(sigma_series) else float("nan")

    rows = []
    for h in horizons:
        series = _rank_ic_frame(analysis_factor, fwd[h], factor_rank=factor_rank)
        report.ic[h] = series
        if series.empty:
            continue
        icir = series.mean() / (series.std() or np.nan) * np.sqrt(TRADING_DAYS / h)
        t_indep, n_indep = independent_t(series, step=h)
        lo, hi = block_bootstrap_ci(series, block=max(5, h))
        rows.append(
            {
                "h": h,
                "mean": series.mean(),
                "std": series.std(),
                "ir": icir,
                "t": newey_west_t(series, lag=max(1, h - 1)),
                "t_indep": t_indep,
                "n_indep": n_indep,
                "ci_lo": lo,
                "ci_hi": hi,
                "win": float((series > 0).mean()),
                "n": int(len(series)),
            }
        )
    report.ic_stats = pd.DataFrame(rows).set_index("h") if rows else pd.DataFrame()

    primary = report.ic.get(primary_horizon, pd.Series(dtype="float64"))
    if not primary.empty:
        window = max(20, min(60, len(primary) // 6 or 20))
        report.rolling_ic = primary.rolling(window, min_periods=max(5, window // 3)).mean()
        report.ic_by_year = primary.groupby(primary.index.year).mean()
        report.ic_by_month = primary.groupby(primary.index.month).mean()
        half = len(primary) // 2
        if half >= 20:
            first, second = primary.iloc[:half], primary.iloc[half:]
            report.ic_half = {
                "first": float(first.mean()),
                "second": float(second.mean()),
                "first_n": int(len(first)),
                "second_n": int(len(second)),
                "same_sign": bool(np.sign(first.mean()) == np.sign(second.mean())),
                "first_end": str(first.index.max().date()),
                "second_start": str(second.index.min().date()),
            }
        if regime is not None:
            aligned = regime.reindex(primary.index).dropna()
            if not aligned.empty:
                report.ic_by_regime = primary.reindex(aligned.index).groupby(aligned).mean()
                report.regime_stats = _regime_table(primary, regime)
                if regime_alt is not None:
                    alt = regime_alt.reindex(primary.index).dropna()
                    if not alt.empty:
                        report.regime_stats_alt = _regime_table(primary, regime_alt)
                tmp = pd.DataFrame({"ic": primary, "regime": regime.reindex(primary.index)}).dropna()
                if not tmp.empty:
                    report.regime_share = (
                        tmp.groupby([tmp.index.year, "regime"]).size().unstack(fill_value=0)
                    )
                    report.regime_share = report.regime_share.div(
                        report.regime_share.sum(axis=1).replace(0, np.nan), axis=0
                    )
                    if "bull" in report.regime_share.columns and report.ic_by_year is not None:
                        merged = pd.concat(
                            [report.ic_by_year.rename("ic"), report.regime_share["bull"].rename("bull")],
                            axis=1,
                        ).dropna()
                        if len(merged) >= 4 and merged["bull"].nunique() > 1:
                            report.regime_corr = float(merged["ic"].corr(merged["bull"]))
                            report.regime_corr_p = _corr_p(report.regime_corr, len(merged))
        report.decay = pd.Series({h: s.mean() for h, s in report.ic.items()})
        report.decay_note = _decay_note(report.decay)

        year_rows = {}
        for year, group in primary.groupby(primary.index.year):
            lo, hi = block_bootstrap_ci(group, block=5)
            year_rows[int(year)] = {
                "mean": float(group.mean()),
                "n": int(len(group)),
                "ci_lo": lo,
                "ci_hi": hi,
            }
        report.ic_by_year_ci = pd.DataFrame(year_rows).T

        month_rows = {}
        for month, group in primary.groupby(primary.index.month):
            lo, hi = block_bootstrap_ci(group, block=5)
            month_rows[int(month)] = {
                "mean": float(group.mean()),
                "n": int(len(group)),
                "ci_lo": lo,
                "ci_hi": hi,
            }
        report.ic_by_month_ci = pd.DataFrame(month_rows).T

    quantile = _quantile_nav(
        analysis_factor, close, open_, groups=groups, horizon=primary_horizon, cost_bps=cost_bps,
        tradeable=mask if exclude_limit_up else None,
    )
    nav, turnover_df, period_returns, period_gross, daily, ls_cost, turnover_mean = quantile
    try:
        import warnings as _w

        with _w.catch_warnings():
            _w.simplefilter("ignore", RuntimeWarning)
            _ret5 = close.pct_change(5, fill_method=None)
            _stds = np.nanstd(_ret5.to_numpy(dtype="float64"), axis=1)
        _stds = _stds[np.isfinite(_stds) & (_stds > 0)]
        report.cross_section_std = float(_stds.mean()) if _stds.size else float("nan")
    except Exception:  # noqa: BLE001
        report.cross_section_std = float("nan")
    # 分层 IC：流动性 / 市值 / 行业，全部在已加载宽表上切片
    try:
        _strata: dict[str, pd.DataFrame] = {}
        if amount is not None:
            _ar = amount.reindex(index=analysis_factor.index, columns=analysis_factor.columns).rank(
                axis=1, pct=True)
            _strata["流动性 L1 后40%"] = _ar <= 0.4
            _strata["流动性 L2 中20%"] = (_ar > 0.4) & (_ar <= 0.6)
            _strata["流动性 L3 前40%"] = _ar > 0.6
        if market_cap is not None:
            _mr = market_cap.reindex(index=analysis_factor.index, columns=analysis_factor.columns).rank(
                axis=1, pct=True)
            _ranges: dict[str, tuple[float, float]] = {}
            _mc_yi = market_cap.reindex(index=analysis_factor.index,
                                        columns=analysis_factor.columns) / 1e8
            for i, label in enumerate(("市值 S1 最小", "市值 S2", "市值 S3", "市值 S4", "市值 S5 最大")):
                lo, hi = i / 5, (i + 1) / 5
                _mask = (_mr > lo) & (_mr <= hi) if i else (_mr <= hi)
                _strata[label] = _mask
                _vals = _mc_yi.where(_mask).stack().dropna()
                if len(_vals) > 100:
                    _ranges[label] = (float(_vals.quantile(0.05)), float(_vals.quantile(0.95)))
            report.size_ranges = _ranges
        if industry is not None:
            _map = industry
            if isinstance(industry, pd.DataFrame) and "symbol" in industry.columns:
                _map = industry.set_index("symbol")["industry"]
            elif isinstance(industry, pd.DataFrame):
                _map = industry.iloc[-1]
            if isinstance(_map, pd.Series):
                # 按成分股数量从多到少取前 12 个行业（原来按 unique 顺序取，
                # 前 12 个可能都是小行业，导致全部被 30 只的门槛过滤掉）
                _counts = _map.reindex(analysis_factor.columns).dropna().value_counts()
                for name in _counts[_counts >= 30].index[:12]:
                    cols = [c for c in analysis_factor.columns if _map.get(c) == name]
                    if len(cols) >= 30:
                        _strata[f"行业 {name}"] = pd.DataFrame(
                            False, index=analysis_factor.index, columns=analysis_factor.columns)
                        _strata[f"行业 {name}"][cols] = True
        if _strata:
            report.strat_ic = _stratified_ic(analysis_factor, fwd[primary_horizon], _strata)
    except Exception:  # noqa: BLE001
        report.strat_ic = None
    report.quantile_factor = analysis_factor
    # 复现自检必须复用同一组参数，否则比较的是两次不同设定，永远"不一致"
    report.quantile_args = {
        "groups": groups,
        "horizon": primary_horizon,
        "cost_bps": cost_bps,
        "tradeable": mask if exclude_limit_up else None,
    }
    report.nav = nav
    report.daily_returns = daily
    report.period_returns = period_returns
    report.period_returns_gross = period_gross
    report.turnover_by_group = turnover_mean
    report.period_returns_net = period_returns
    if not period_returns.empty and not period_gross.empty:
        # 分层优劣用「每期平均收益」，不用复利终值（避免波动拖累污染单调性）。
        # 单调性用**毛收益**：净收益被各组换手差异污染，会把方向判断带偏。
        means_net = period_returns.mean()
        means_gross = period_gross.mean()
        report.quantile_returns = {str(k): float(v) for k, v in means_net.items()}
        report.quantile_returns_gross = {str(k): float(v) for k, v in means_gross.items()}
        report.quantile_ci = {}
        for col in period_returns.columns:
            series = period_returns[col].dropna()
            if len(series) >= 10:
                se = series.std(ddof=1) / np.sqrt(len(series))
                report.quantile_ci[col] = (float(series.mean() - 1.96 * se), float(series.mean() + 1.96 * se))
        if len(means_gross) >= 3:
            report.monotonicity, report.monotonicity_p = diag.group_monotonicity(means_gross)
            report.monotonicity_net, _ = diag.group_monotonicity(means_net)

        q_high, q_low = f"Q{groups}", "Q1"
        # 顶档/底档特异度：给“单尾断崖”一个独立于 Spearman 的表达。
        try:
            _mid_labels = [f"Q{i}" for i in range(2, max(2, groups))]
            _mid = float(means_gross.reindex(_mid_labels).median())
            _upper = float(means_gross[q_high] - _mid)
            _lower = float(means_gross[q_low] - _mid)
            _denom = abs(_upper) + abs(_lower)
            _ratio = max(abs(_upper), abs(_lower)) / _denom if _denom > 1e-12 else 0.0
            _upper_diff = (period_gross[q_high] - period_gross[_mid_labels].mean(axis=1)).dropna()
            _lower_diff = (period_gross[q_low] - period_gross[[c for c in period_gross.columns if c != q_low]].mean(axis=1)).dropna()
            def _t_of(series: pd.Series) -> float:
                if len(series) < 3 or series.std(ddof=1) == 0:
                    return float("nan")
                return float(series.mean() / (series.std(ddof=1) / np.sqrt(len(series))))
            _tu, _tl = _t_of(_upper_diff), _t_of(_lower_diff)
            _tt = max(abs(_tu), abs(_tl))
            report.tail_specificity = {
                "ratio": float(_ratio),
                "upper": _upper,
                "lower": _lower,
                "mid": _mid,
                "upper_t": _tu,
                "lower_t": _tl,
                "t": _tt if np.isfinite(_tt) else 0.0,
                "score": float(np.clip(_ratio * np.clip(abs(_tt) / 2.0, 0, 1), 0, 1))
                if np.isfinite(_tt) else 0.0,
            }
        except Exception as exc:  # noqa: BLE001
            logger.warning("顶档特异度计算失败: %s", exc)
            report.tail_specificity = {}
        gross_spread = (period_gross[q_high] - period_gross[q_low]).dropna()
        cost_series = (
            (turnover_df.get(q_high, pd.Series(dtype="float"))
             + turnover_df.get(q_low, pd.Series(dtype="float"))) * cost_bps / 10000.0
        ).dropna()
        # 多空净价差 = 毛价差 − 两条腿的换手成本。不能用 Q5_net − Q1_net：
        # 两条腿成本相同会互相抵消，等于漏算空头腿成本。
        net_spread = (gross_spread - cost_series).dropna()
        report.spread_gross = diag.spread_stats(gross_spread)
        report.spread_net = diag.spread_stats(net_spread)
        report.cost_mean = float(cost_series.mean()) if len(cost_series) else float("nan")

        # 去极值敏感性：1%/99% 横截面缩尾，回答“G5 断崖是不是极端值造成”
        try:
            _wf = _winsorize_frame(analysis_factor, 0.01, 0.99)
            _wq = _quantile_nav(
                _wf, close, open_, groups=groups, horizon=primary_horizon,
                cost_bps=cost_bps,
            )
            _, _wto, _wpnet, _wpgross, _, _, _ = _wq
            _wg = _wpgross.mean()
            _wn = _wpnet.mean()
            _wcost = (
                (_wto.get(q_high, pd.Series(dtype="float64"))
                 + _wto.get(q_low, pd.Series(dtype="float64")))
                * cost_bps / 10000.0
            ).dropna()
            _wls = (_wpgross[q_high] - _wpgross[q_low] - _wcost).dropna()
            report.winsor = {
                "g1": float(_wg.get(q_low, np.nan)),
                "g5": float(_wg.get(q_high, np.nan)),
                "spread_gross": float(_wg.get(q_high, np.nan) - _wg.get(q_low, np.nan)),
                "spread_net": float(_wls.mean()) if len(_wls) else np.nan,
                "spread_net_t": float(newey_west_t(_wls, lag=max(1, primary_horizon - 1)))
                if len(_wls) > 2 else np.nan,
                "n_periods": int(len(_wpgross)),
            }
        except Exception as exc:  # noqa: BLE001
            logger.warning("去极值敏感性计算失败: %s", exc)
            report.winsor = {}

        try:
            report.return_winsor = _winsor_group_returns(
                analysis_factor, fwd[primary_horizon],
                groups=groups, horizon=primary_horizon,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("收益缩尾敏感性计算失败: %s", exc)
            report.return_winsor = {}

        # 多持有期对比（5/10/20 日）＋ 10 日市值中性化版本
        _hs_rows = []
        _results = {}
        for _h in (5, 10, 20, 60):
            try:
                if _h == primary_horizon:
                    _g, _n = period_gross, period_returns
                    _to = float(turnover_df.mean().mean()) if len(turnover_df) else float("nan")
                else:
                    _nav_h, _to_h, _pr_h, _pg_h, _daily_h, _lsc_h, _tmean = _quantile_nav(
                        analysis_factor, close, open_, groups=groups, horizon=_h,
                        cost_bps=cost_bps,
                        tradeable=mask if exclude_limit_up else None,
                    )
                    _g, _n = _pg_h, _pr_h
                    _to = float(_to_h.mean().mean()) if len(_to_h) else float("nan")
                _hi, _lo = f"Q{groups}", "Q1"
                _gs = float((_g[_hi] - _g[_lo]).mean())
                _ns = float((_n[_hi] - _n[_lo]).mean())
                _hs_rows.append({
                    "horizon": _h, "variant": "原始",
                    "gross": _gs, "net": _ns, "turnover": _to,
                })
                _results[_h] = (_gs, _ns)
            except Exception as exc:  # noqa: BLE001
                logger.warning("持有期 %s 计算失败: %s", _h, exc)

        # 10 日 + 市值中性化
        try:
            _neu = neutralize_factor(analysis_factor, market_cap)
            _nav_n, _to_n, _pr_n, _pg_n, _d_n, _l_n, _tm_n = _quantile_nav(
                _neu, close, open_, groups=groups, horizon=10, cost_bps=cost_bps,
                tradeable=mask if exclude_limit_up else None,
            )
            _hi, _lo = f"Q{groups}", "Q1"
            _hs_rows.append({
                "horizon": 10, "variant": "市值中性",
                "gross": float((_pg_n[_hi] - _pg_n[_lo]).mean()),
                "net": float((_pr_n[_hi] - _pr_n[_lo]).mean()),
                "turnover": float(_to_n.mean().mean()) if len(_to_n) else float("nan"),
            })
        except Exception as exc:  # noqa: BLE001
            logger.warning("10 日中性化对比失败: %s", exc)

        if need_cross_extra:
            report.horizon_spreads = pd.DataFrame(_hs_rows)
        if need_cross_core:
            report.filter_test = _filter_test(period_returns, primary_horizon)
            report.filter_test_opposite = _filter_test_side(
                period_returns, primary_horizon, f"Q{groups}"
            )
        if len(net_spread) > 10:
            ls_nav = (1 + net_spread).cumprod()
            report.ls_nav = ls_nav
            report.ls_daily = net_spread
            report.drawdown = ls_nav / ls_nav.cummax() - 1
            report.perf = _period_performance(net_spread, primary_horizon)
            report.annual_returns = _annual_returns(ls_nav)
            if need_cross_extra:
                report.annual_ic_return = _annual_ic_return(report.ic_by_year, report.annual_returns)
            report.long_short = {
                "total": report.perf.get("total", np.nan),
                "ann": report.perf.get("ann", np.nan),
                "sharpe": report.perf.get("sharpe", np.nan),
                "mdd": report.perf.get("mdd", np.nan),
                "calmar": report.perf.get("calmar", np.nan),
                "win": report.perf.get("win", np.nan),
                "geo_period": report.perf.get("ann", np.nan),
                "ls_spread_mean": float(net_spread.mean()),
                "gross_spread_mean": report.spread_gross["mean"],
                "cost_mean": report.cost_mean,
                "net_spread_mean": report.spread_net["mean"],
                "spread_t": report.spread_net["t"],
                "spread_gross_t": report.spread_gross["t"],
            }
            if report.filter_test:
                report.filter_test["spread_mean"] = float(net_spread.mean())
                report.filter_test["spread_se"] = report.spread_net["se"]
                report.filter_test["spread_gross_mean"] = report.spread_gross["mean"]
                report.filter_test["spread_gross_se"] = report.spread_gross["se"]
    report.turnover = (
        float(turnover_mean.get(f"Q{groups}", turnover_mean.mean()))
        if not turnover_mean.empty else float("nan")
    )
    if quality is not None:
        report.data_quality["clean"] = {
            "g1": float(report.quantile_returns_gross.get("Q1", np.nan)),
            "g5": float(report.quantile_returns_gross.get(f"Q{groups}", np.nan)),
            "spread_gross": float((report.spread_gross or {}).get("mean", np.nan)),
            "spread_net": float((report.spread_net or {}).get("mean", np.nan)),
            "spread_net_t": float((report.spread_net or {}).get("t", np.nan)),
            "monotonicity": float(report.monotonicity),
            "turnover": float(report.turnover),
            "n_periods": int(len(report.period_returns)) if report.period_returns is not None else 0,
        }
    if benchmark is not None and not benchmark.empty:
        bench = benchmark.reindex(close.index).ffill().dropna()
        if not bench.empty:
            report.benchmark_nav = bench / bench.iloc[0]

    if need_cross_extra and (market_cap is not None or industry is not None):
        report.ic_neutral = neutralized_ic(
            analysis_factor, fwd[primary_horizon], market_cap, industry
        )
        if industry is not None:
            ind = report.ic_neutral.loc["行业中性", "mean"] if "行业中性" in report.ic_neutral.index else np.nan
            report.industry_neutral_ic = float(ind)

    if need_cross_extra and market_cap is not None:
        ic_by_size = {}
        caps = market_cap.reindex(index=factor.index, columns=factor.columns)
        for label, lo, hi in (("小市值", 0.0, 1 / 3), ("中市值", 1 / 3, 2 / 3), ("大市值", 2 / 3, 1.0)):
            rank = caps.rank(axis=1, pct=True)
            masked = analysis_factor.where((rank >= lo) & (rank <= hi))
            series = _rank_ic_frame(masked, fwd[primary_horizon])
            if len(series) > 20:
                ic_by_size[label] = float(series.mean())
        report.ic_by_size = pd.Series(ic_by_size) if ic_by_size else None

    if need_cross_extra and industry is not None:
        industry = industry.reindex(index=factor.index, columns=factor.columns)
        ic_by_ind = {}
        for name in pd.unique(industry.iloc[-1].dropna()):
            if str(name) in ("未知", "未分类"):
                continue
            mask_ind = industry == name
            masked = analysis_factor.where(mask_ind)
            series = _rank_ic_frame(masked, fwd[primary_horizon], min_names=8)
            if len(series) > 20:
                ic_by_ind[str(name)] = float(series.mean())
        if ic_by_ind:
            report.ic_by_industry = pd.Series(ic_by_ind).sort_values(ascending=False)

    if need_cross_extra:
        report.tradeability = _tradeability(
            analysis_factor, close, open_, horizon=primary_horizon, is_st=is_st
        )
    if full and need_cross_extra:
        report.sell_side = _sell_side_summary(
            analysis_factor, close, open_, fwd[primary_horizon], horizon=primary_horizon, groups=groups, is_st=is_st
        )
        report.cost_table = _cost_sensitivity(
            analysis_factor, close, open_, groups=groups, horizon=primary_horizon,
            tradeable=mask if exclude_limit_up else None, quantile=quantile,
        )
        report.capacity = _capacity(amount, analysis_factor, primary_horizon, groups) if amount is not None else {}
    if full and need_cross_core:
        inc, corr = _incremental(analysis_factor, fwd[primary_horizon], peers)
        report.incrementality = inc
        report.peers = corr

    if report.ic_by_regime is not None and len(report.ic_by_regime) > 1:
        if report.ic_by_regime.min() < 0 < report.ic_by_regime.max():
            report.warnings.append(
                "因子在部分市场状态下 IC 为负（"
                + "、".join(f"{k}:{v:+.3f}" for k, v in report.ic_by_regime.items())
                + "），存在状态依赖"
            )
    if report.coverage < 0.7:
        report.warnings.append(f"覆盖率仅 {report.coverage:.0%}（< 70%），横截面代表性不足")
    if report.turnover == report.turnover and report.turnover > 0.6:
        report.warnings.append(f"Top 组换手 {report.turnover:.0%} 偏高，成本敏感")
    if report.perf.get("mdd", 0) < -0.40:
        report.warnings.append(
            f"多空最大回撤 {report.perf['mdd']:.1%}，波动极大；需按回撤预算决定是否可用"
        )

    # ---- 结构化自检：结论必须能被程序复核，而不是只写在正文里 ----
    ic5 = float(report.ic_stats.loc[primary_horizon, "mean"]) if primary_horizon in report.ic_stats.index else float("nan")
    t5 = float(report.ic_stats.loc[primary_horizon, "t_indep"]) if primary_horizon in report.ic_stats.index else float("nan")
    if not np.isfinite(t5):
        t5 = float(report.ic_stats.loc[primary_horizon, "t"]) if primary_horizon in report.ic_stats.index else float("nan")
    gross = report.spread_gross or {}
    report.transmission = diag.ic_spread_transmission(ic5, report.sigma_5d, gross.get("mean", np.nan)).as_dict()
    checks = [
        diag.rho_spread_identity(report.monotonicity, gross.get("mean", np.nan), groups),
        diag.ic_spread_transmission(ic5, report.sigma_5d, gross.get("mean", np.nan)),
        diag.tradeability_interval(gross, report.cost_mean),
        diag.direction_prior(ic5, t5, report.direction),
    ]
    _repro = getattr(report, "_repro_check", None)
    report.diagnostics = list(checks) + ([_repro] if _repro is not None else [])
    report.attribution = diag.attribute_failure(report)
    for check in checks:
        if check.status == "fail":
            report.warnings.append(f"自检失败[{check.check_id}]：{check.actual}")
        elif check.status == "warn":
            report.warnings.append(f"自检提示[{check.check_id}]：{check.actual}")
    # 标题/样本区间按“第一个有有效 IC 的交易日”收口，避免 252 日因子把 2017 写成样本期。
    _ic5 = (report.ic or {}).get(5)
    if _ic5 is not None and len(_ic5):
        _first_ic = _ic5.first_valid_index()
        if _first_ic is not None:
            report.start = max(report.start, _first_ic)
    return report


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


def _cost_sensitivity(
    factor: pd.DataFrame,
    close: pd.DataFrame,
    open_: pd.DataFrame,
    *,
    groups: int,
    horizon: int,
    tradeable: pd.DataFrame | None,
    quantile: QuantileResult | None = None,
) -> pd.DataFrame:
    # 分组和毛收益与 bps 无关；一次分组即可，后续只按换手派生不同成本。
    q = quantile or _quantile_nav(
        factor, close, open_, groups=groups, horizon=horizon, cost_bps=0.0,
        tradeable=tradeable,
    )
    turnover_df = q.turnover_df
    periods_gross = q.periods_gross
    q_hi = f"Q{groups}"
    gross_spread = (
        periods_gross.get(q_hi, pd.Series(dtype="float"))
        - periods_gross.get("Q1", pd.Series(dtype="float"))
    )
    legs_turnover = (
        turnover_df.get(q_hi, pd.Series(dtype="float"))
        + turnover_df.get("Q1", pd.Series(dtype="float"))
    )
    rows = {}
    for bps in (0.0, 10.0, 20.0, 30.0, 50.0):
        spread = (gross_spread - legs_turnover * bps / 10000.0).dropna()
        perf = _period_performance(spread, horizon)
        rows[f"{bps:.0f}bp"] = {
            "ann": perf.get("ann", np.nan),
            "sharpe": perf.get("sharpe", np.nan),
            "mdd": perf.get("mdd", np.nan),
        }
    return pd.DataFrame(rows).T



_LS_COST = 0.002
_LS_STARTS = ("2022-01-01", "2023-01-01", "2024-01-01", "2025-01-01")
_LS_INDEX = ((5e10, "000300"), (1e10, "000905"), (0.0, "000852"))


def _norm_code(c) -> str:
    c = str(c).strip()
    if c.endswith(".0"):
        c = c[:-2]
    return c.zfill(6) if c.isdigit() else c


def _index_series(panel, code):
    try:
        from research.data.loader import _query

        df = _query(f"select ts, close from ohlcv_1d where code='{code}' and type='idx' order by ts")
        if df is None or df.empty:
            return None
        df["ts"] = pd.to_datetime(df["ts"]).dt.tz_localize(None)
        return df.set_index("ts")["close"].astype("float64").reindex(panel.close.index).ffill()
    except Exception:  # noqa: BLE001
        return None


def _industry_lookup(panel):
    try:
        from research.data.loader import _query

        df = _query(
            "select code, last(industry) as name from stock_basic_info "
            "where industry is not null group by code")
        if df is not None and not df.empty:
            return {_norm_code(c): str(n) for c, n in zip(df["code"], df["name"])}
    except Exception:  # noqa: BLE001
        pass
    imap = panel.industry
    if isinstance(imap, pd.DataFrame):
        imap = imap.iloc[-1]
    if imap is None:
        return {}
    return {_norm_code(k): str(v) for k, v in imap.items()}


def _ls_periods(factor, panel, direction, horizon, groups):
    sig = (factor * int(direction)).reindex(index=panel.close.index, columns=panel.close.columns)
    close, open_ = panel.close, panel.open
    out = []
    for i in range(0, len(close) - horizon - 2, horizon):
        entry, exit_ = open_.iloc[i + 1], close.iloc[i + horizon]
        ret = (exit_ / entry - 1.0).replace([np.inf, -np.inf], np.nan)
        s = sig.iloc[i]
        ok = s.notna() & ret.notna()
        if ok.sum() < 200:
            continue
        q = pd.qcut(s[ok].rank(method="first"), groups, labels=False)
        top = ret[ok][q == groups - 1].index
        out.append({"i": i, "top": top, "ret": ret[ok],
                    "r_top": float(ret[top].mean()) - _LS_COST, "date": close.index[i]})
    return out


def _ls_stats(r_top, r_bench, *, periods_per_year: float = 12.0):
    """多头/基准统计。频率由 ``periods_per_year`` 明确传入，避免 5 日频被当成月频。"""
    a = np.asarray(r_top, dtype="float64")
    b = np.asarray(r_bench, dtype="float64")
    ex = a - b
    eq = np.cumprod(1 + a)
    beta = np.cov(a, b)[0, 1] / np.var(b, ddof=1) if len(a) > 2 else np.nan
    ppy = float(periods_per_year) if np.isfinite(periods_per_year) and periods_per_year > 0 else 12.0
    return {
        "cagr": float(eq[-1] ** (ppy / max(1, len(a))) - 1),
        "bench": float(np.cumprod(1 + b)[-1] ** (ppy / max(1, len(a))) - 1),
        "excess": float(ex.mean() * ppy),
        "te": float(ex.std(ddof=1) * np.sqrt(ppy)),
        "ir": float(ex.mean() / ex.std(ddof=1) * np.sqrt(ppy)) if ex.std(ddof=1) else np.nan,
        "beta": float(beta),
        "alpha": float((a.mean() - beta * b.mean()) * ppy),
        "t": float(ex.mean() / (ex.std(ddof=1) / np.sqrt(len(ex)))) if ex.std(ddof=1) else np.nan,
        "mdd": float((eq / np.maximum.accumulate(eq) - 1).min()),
        "win": float((ex > 0).mean()),
        "n": int(len(a)),
    }


# ---------------- 多头视角：三层基准 / 多起点 / 容量 / 持仓分布
def long_only_stats(factor, panel, direction, horizon=20, groups=5) -> dict:
    per = _ls_periods(factor, panel, direction, horizon, groups)
    if len(per) < 12:
        return {}

    cap = panel.market_cap
    amount = panel.amount
    ind_map = _industry_lookup(panel)

    # 每期持仓的市值中位数 → 决定指数基准
    med_caps = []
    for p in per:
        if cap is not None:
            v = cap.iloc[p["i"]][p["top"]]
            med_caps.append(float(np.nanmedian(v)) if len(v) else np.nan)
    med_cap = float(np.nanmedian(med_caps)) if med_caps else np.nan
    idx_code = next(c for th, c in _LS_INDEX if med_cap > th) if med_cap == med_cap else "000905"
    idx = _index_series(panel, idx_code)

    r_top, r_ew, r_idx, r_style = [], [], [], []
    for p in per:
        r_top.append(p["r_top"])
        r_ew.append(float(p["ret"].mean()))
        i = p["i"]
        if idx is not None and np.isfinite(idx.iloc[i + horizon]) and np.isfinite(idx.iloc[i + 1]):
            r_idx.append(float(idx.iloc[i + horizon] / idx.iloc[i + 1] - 1))
        else:
            r_idx.append(float(p["ret"].mean()))
        # 风格基准：按持仓的市值分桶比例，从全市场同桶里取等权收益
        if cap is not None:
            allv = cap.iloc[i].reindex(p["ret"].index)
            topv = allv.reindex(p["top"])
            bins = pd.cut(topv, [-np.inf, 5e9, 1e10, 5e10, np.inf])
            allbins = pd.cut(allv, [-np.inf, 5e9, 1e10, 5e10, np.inf])
            w = bins.value_counts(normalize=True)
            val, wsum = 0.0, 0.0
            for b, wt in w.items():
                sub = p["ret"][allbins == b]
                if len(sub) > 20:
                    val += float(sub.mean()) * float(wt)
                    wsum += float(wt)
            r_style.append(val / wsum if wsum > 0 else float(p["ret"].mean()))
        else:
            r_style.append(float(p["ret"].mean()))

    dates = [p["date"] for p in per]
    ppy = TRADING_DAYS / max(1, int(horizon))
    res = {
        "periods": len(per),
        "holdings": int(np.median([len(p["top"]) for p in per])),
        "index_code": idx_code,
        "median_cap": med_cap,
        "ew": _ls_stats(r_top, r_ew, periods_per_year=ppy),
        "index": _ls_stats(r_top, r_idx, periods_per_year=ppy),
        "style": _ls_stats(r_top, r_style, periods_per_year=ppy),
        "capacity": float(np.nanmedian([float(amount.iloc[p["i"]][p["top"]].median()) for p in per])) * 0.05 * int(np.median([len(p["top"]) for p in per]))
        if amount is not None else np.nan,
        "oos": [],
    }

    for st in _LS_STARTS:
        mask = [d >= pd.Timestamp(st) for d in dates]
        if sum(mask) < 12:
            continue
        sub = lambda arr: [v for v, m in zip(arr, mask) if m]
        s = _ls_stats(sub(r_top), sub(r_ew), periods_per_year=ppy)
        s["start"] = st
        res["oos"].append(s)

    # 持仓分布
    cnt: dict[str, int] = {}
    for p in per:
        for c in p["top"]:
            cnt[c] = cnt.get(c, 0) + 1
    total = sum(cnt.values()) or 1
    inds: dict[str, int] = {}
    for c, n in cnt.items():
        inds[ind_map.get(_norm_code(c), "未映射")] = inds.get(ind_map.get(_norm_code(c), "未映射"), 0) + n
    res["industry"] = sorted(((k, v / total) for k, v in inds.items()), key=lambda x: -x[1])[:8]
    res["unmapped"] = inds.get("未映射", 0) / total

    if cap is not None:
        last = cap.iloc[-1]
        buckets = {"小盘<100亿": 0, "中盘100-500亿": 0, "大盘>500亿": 0, "未知": 0}
        for c, n in cnt.items():
            v = last.get(c, np.nan)
            if v != v:
                buckets["未知"] += n
            elif v > 5e10:
                buckets["大盘>500亿"] += n
            elif v > 1e10:
                buckets["中盘100-500亿"] += n
            else:
                buckets["小盘<100亿"] += n
        res["size"] = [(k, v / total) for k, v in buckets.items() if v]
    else:
        res["size"] = []
    return res

