"""Neutralisation / stratified IC (copied verbatim from research.report.metrics)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from .ic import _rank_ic_frame
from .stats import TRADING_DAYS, _zscore, newey_west_t
from .winsor import _winsorize


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
