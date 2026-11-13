"""Regime slicing / decay note (copied verbatim from research.report.metrics)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from .stats import block_bootstrap_ci, newey_west_t


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
