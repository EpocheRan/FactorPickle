"""Winsorisation helpers (copied verbatim from research.report.metrics)."""

from __future__ import annotations

import numpy as np
import pandas as pd


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

def _trimmed_group_returns(
    factor: pd.DataFrame,
    forward: pd.DataFrame,
    *,
    groups: int = 5,
    horizon: int = 5,
    lower: float = 0.01,
    upper: float = 0.99,
) -> dict:
    """组内收益裁剪后的分组均值（core 扩展，research 只提供 winsor 版本）。

    与 :func:`_winsor_group_returns` 的区别：这里直接丢弃 [lower, upper] 分位
    之外的观测（trim），而不是把它们压缩到边界（winsor）。用于 Alpha 的尾部
    稳健性诊断，保持与 research 相同的分组/调仓节奏与最小样本规则。
    """
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
                kept = vals[(vals >= lo) & (vals <= hi)]
                if len(kept):
                    buckets[g].append(float(kept.mean()))
        i += horizon
    means = {f"Q{g + 1}": float(np.mean(v)) for g, v in buckets.items() if v}
    if "Q1" in means and f"Q{groups}" in means:
        means["spread"] = means[f"Q{groups}"] - means["Q1"]
    return means

