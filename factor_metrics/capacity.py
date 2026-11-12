"""Capacity estimation (copied verbatim from research.report.metrics)."""

from __future__ import annotations

import numpy as np
import pandas as pd


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
