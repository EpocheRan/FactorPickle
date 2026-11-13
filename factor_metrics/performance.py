"""Performance / period statistics (copied verbatim from research.report.metrics)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from .stats import TRADING_DAYS


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


def _annual_ic_return(ic_by_year: pd.Series | None, annual: pd.Series | None) -> pd.DataFrame | None:
    if ic_by_year is None or annual is None:
        return None
    frame = pd.concat([ic_by_year.rename("ic"), annual.rename("ret")], axis=1).dropna()
    if frame.empty:
        return None
    return frame


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
