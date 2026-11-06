"""四版本股票池敏感性：同一因子、同一区间、同一持有期。

四个版本：
- ALL：当前报告样本；
- B：排除 BSE；
- C：investable（ST 用当前状态，非 PIT；上市 >=365 天；20 日成交额中位数 >=2000 万；价格 >=2）；
- D：high-liquidity（在 C 的基础上，每日按 20 日成交额取前 200）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from research.data.db import query
from . import metrics


def _metadata() -> pd.DataFrame:
    try:
        frame = query(
            "select code, last(market) as market, last(is_st) as is_st, "
            "last(list_date) as list_date from stock_basic_info group by code"
        )
        frame["code"] = frame["code"].astype(str)
        return frame.set_index("code")
    except Exception:  # noqa: BLE001 - 元数据失败时退化为仅按代码前缀排除 BSE
        return pd.DataFrame()


def _rank_ic(signal: pd.DataFrame, forward: pd.DataFrame, min_names: int = 30) -> pd.Series:
    valid = signal.notna() & forward.notna()
    counts = valid.sum(axis=1)
    left = signal.where(valid).rank(axis=1)
    right = forward.where(valid).rank(axis=1)
    ic = left.corrwith(right, axis=1)
    return ic.where(counts >= min_names).dropna()


def _one(
    name: str,
    *,
    signal: pd.DataFrame,
    forward: pd.DataFrame,
    close: pd.DataFrame,
    open_: pd.DataFrame,
    groups: int,
    horizon: int,
    cost_bps: float,
    tradeable: pd.DataFrame | None,
) -> dict:
    if tradeable is not None:
        signal = signal.where(tradeable)
    valid = signal.notna() & forward.notna()
    counts = valid.sum(axis=1)
    ic = _rank_ic(signal, forward)
    ic_t = metrics.newey_west_t(ic, lag=max(0, horizon - 1)) if len(ic) > 2 else float("nan")
    _, _, pg, pnet, _, _, turnover = metrics._quantile_nav(
        signal, close, open_, groups=groups, horizon=horizon,
        cost_bps=cost_bps, tradeable=tradeable,
    )
    gross = pg.mean() if not pg.empty else pd.Series(dtype="float64")
    net = pnet.mean() if not pnet.empty else pd.Series(dtype="float64")
    vals = signal.stack().dropna()
    return {
        "universe": name,
        "symbols": int(signal.shape[1]),
        "avg_names": float(counts.mean()) if len(counts) else float("nan"),
        "zero_share": float((vals == 0).mean()) if len(vals) else float("nan"),
        "ic": float(ic.mean()) if len(ic) else float("nan"),
        "ic_t": float(ic_t),
        "g1": float(gross.get("Q1", np.nan)),
        "g5": float(gross.get(f"Q{groups}", np.nan)),
        "spread_gross": float(gross.get(f"Q{groups}", np.nan) - gross.get("Q1", np.nan)),
        "spread_net": float(net.get(f"Q{groups}", np.nan) - net.get("Q1", np.nan)),
        "turnover": float(turnover.mean().mean()) if not turnover.empty else float("nan"),
    }


def baseline_from_report(report, factor: pd.DataFrame) -> dict:
    """把主报告已经算好的 ALL 结果转成敏感性表的第一行。"""
    stats = report.ic_stats
    primary = 5 if 5 in stats.index else (stats.index[0] if len(stats) else None)
    row = stats.loc[primary] if primary is not None else None
    gross = report.quantile_returns_gross or {}
    net = report.quantile_returns or {}
    turnover = (
        float(report.turnover_by_group.mean())
        if getattr(report, "turnover_by_group", None) is not None
        and len(report.turnover_by_group) else float(report.turnover)
    )
    finite = factor.notna().sum().sum()
    zeros = (factor == 0).sum().sum()
    q_keys = sorted(
        gross, key=lambda k: int(k[1:]) if str(k).startswith("Q") and str(k)[1:].isdigit() else 0
    )
    return {
        "universe": "ALL",
        "symbols": int(report.n_symbols),
        "avg_names": float(report.n_symbols * report.coverage),
        "zero_share": float(zeros / finite) if finite else float("nan"),
        "ic": float(row["mean"]) if row is not None else float("nan"),
        "ic_t": float(row["t"]) if row is not None else float("nan"),
        "g1": float(gross.get("Q1", np.nan)),
        "g5": float(gross.get(q_keys[-1], np.nan)) if q_keys else float("nan"),
        "spread_gross": float((report.spread_gross or {}).get("mean", np.nan)),
        "spread_net": float((report.spread_net or {}).get("mean", np.nan)),
        "turnover": turnover,
    }


def compute(
    panel,
    factor: pd.DataFrame,
    *,
    groups: int = 5,
    horizon: int = 5,
    cost_bps: float = 20.0,
    high_liquidity_n: int = 200,
    start=None,
    end=None,
    baseline: dict | None = None,
) -> list[dict]:
    """返回四个版本的 IC / 分组收益 / 价差 / 换手。"""
    dates = factor.index
    if start is not None:
        dates = dates[dates >= pd.Timestamp(start)]
    if end is not None:
        dates = dates[dates <= pd.Timestamp(end)]
    close = panel.close.reindex(index=dates, columns=factor.columns)
    open_ = panel.open.reindex(index=dates, columns=factor.columns)
    signal = factor.reindex(index=dates, columns=close.columns)
    forward = close.shift(-horizon) / open_.shift(-1) - 1

    meta = _metadata()
    codes = close.columns.astype(str)
    markets = (meta["market"].reindex(codes).astype(str) if "market" in meta else pd.Series("", index=codes))
    is_st = (meta["is_st"].reindex(codes).fillna(False).astype(bool)
             if "is_st" in meta else pd.Series(False, index=codes))
    list_date = (pd.to_datetime(meta["list_date"].reindex(codes), errors="coerce")
                 .dt.tz_localize(None)
                 if "list_date" in meta else pd.Series(pd.NaT, index=codes))

    bse = (markets == "BSE") | pd.Series(codes.str.startswith(("4", "8", "9")), index=codes)
    amount = panel.amount.reindex(index=dates, columns=close.columns)
    close_raw = panel.close_raw.reindex(index=dates, columns=close.columns)
    liq = amount.rolling(20, min_periods=10).median().shift(1)
    liq_ok = liq >= 20_000_000
    price_ok = close_raw >= 2.0
    age_ok = pd.DataFrame(True, index=dates, columns=close.columns)
    for code in close.columns:
        listed = list_date.get(code)
        if pd.notna(listed):
            age_ok[code] = dates >= listed + pd.Timedelta(days=365)

    investable = pd.DataFrame(True, index=dates, columns=close.columns)
    investable.loc[:, bse[bse].index] = False
    investable.loc[:, is_st[is_st].index] = False
    investable &= age_ok & liq_ok.fillna(False) & price_ok.fillna(False)

    liq_rank = liq.rank(axis=1, ascending=False, method="first")
    high = investable & (liq_rank <= int(high_liquidity_n))

    all_cols = list(close.columns)
    b_cols = [c for c in close.columns if not bool(bse.get(c, False))]

    out = [baseline] if baseline is not None else [
        _one("ALL", signal=signal, forward=forward, close=close, open_=open_,
             groups=groups, horizon=horizon, cost_bps=cost_bps, tradeable=None),
    ]
    out += [
        _one("EX_BSE", signal=signal[b_cols], forward=forward[b_cols],
             close=close[b_cols], open_=open_[b_cols],
             groups=groups, horizon=horizon, cost_bps=cost_bps, tradeable=None),
        _one("INVESTABLE", signal=signal, forward=forward, close=close, open_=open_,
             groups=groups, horizon=horizon, cost_bps=cost_bps, tradeable=investable),
        _one("HIGH_LIQ", signal=signal, forward=forward, close=close, open_=open_,
             groups=groups, horizon=horizon, cost_bps=cost_bps, tradeable=high),
    ]
    return out


__all__ = ["compute"]
