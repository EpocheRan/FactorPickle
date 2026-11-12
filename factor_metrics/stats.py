"""Calibrated statistical primitives (copied verbatim from research.report).

Source of truth: ``research/report/metrics.py`` and
``research/report/diagnostics.py`` at the A-full baseline.  These implementations
are the ones the production factor report has been calibrated against; do not
change their numerical behaviour without a report-regression update.
"""

from __future__ import annotations

import re

import numpy as np
import pandas as pd

TRADING_DAYS = 242


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


def _corr_p(r: float, n: int) -> float:
    """Fisher z 近似的相关系数双尾 p 值（不依赖 scipy）。"""
    if not np.isfinite(r) or n < 4:
        return float("nan")
    from math import atanh, erfc, sqrt

    z = atanh(max(-0.999999, min(0.999999, r))) * sqrt(n - 3)
    return float(erfc(abs(z) / sqrt(2)))


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


def spearman(values: Sequence[float], labels: Sequence[float] | None = None) -> float:
    """无 scipy 的 Spearman 相关；``labels`` 缺省为等距组序号 1..n。"""
    arr = np.asarray(values, dtype="float64")
    ok = np.isfinite(arr)
    arr = arr[ok]
    if arr.size < 3:
        return float("nan")
    if labels is None:
        lab = np.arange(1, arr.size + 1, dtype="float64")
    else:
        lab = np.asarray(labels, dtype="float64")[ok]
    if np.unique(arr).size < 2 or np.unique(lab).size < 2:
        return float("nan")
    rho = float(pd.Series(arr).rank().corr(pd.Series(lab).rank()))
    return rho


def spearman_p(rho: float, n: int) -> float:
    """Spearman ρ 的 t 近似双尾 p 值（n 为组数，仅作量级参考）。"""
    if not np.isfinite(rho) or n < 3:
        return float("nan")
    rho = max(-0.999999, min(0.999999, rho))
    from math import erfc, sqrt

    t = rho * sqrt((n - 2) / (1 - rho * rho))
    # t 分布尾概率用正态近似；n=5 时只用于提示，不作显著性结论
    return float(erfc(abs(t) / sqrt(2)))


def group_monotonicity(means: dict[str, float] | pd.Series) -> tuple[float, float]:
    """按 Q1..Qn 顺序计算有符号单调性 ρ 与近似 p 值。"""
    if isinstance(means, dict):
        keys = sorted(means, key=lambda k: int(re.sub(r"\D", "", k) or 0))
        values = [means[k] for k in keys]
    else:
        values = list(means.to_numpy())
    rho = spearman(values)
    return rho, spearman_p(rho, len(values))


def spread_stats(series: pd.Series) -> dict:
    """期均值、标准误、t 与 95% CI（价差用每期收益，不用年化）。"""
    clean = series.replace([np.inf, -np.inf], np.nan).dropna()
    n = len(clean)
    if n < 3:
        return {
            "mean": float(clean.mean()) if n else float("nan"),
            "se": float("nan"),
            "t": float("nan"),
            "ci_lo": float("nan"),
            "ci_hi": float("nan"),
            "n": int(n),
        }
    mean = float(clean.mean())
    se = float(clean.std(ddof=1) / np.sqrt(n))
    return {
        "mean": mean,
        "se": se,
        "t": float(mean / se) if se > 0 else float("nan"),
        "ci_lo": float(mean - 1.96 * se),
        "ci_hi": float(mean + 1.96 * se),
        "n": int(n),
    }
