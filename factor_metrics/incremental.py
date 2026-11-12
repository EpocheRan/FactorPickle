"""Peer-correlation / residual-IC incrementality (copied verbatim from research.report.metrics)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from .ic import _rank_ic_frame


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
