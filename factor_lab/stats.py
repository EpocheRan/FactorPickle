"""Small statistics helpers used by audit and orthogonality."""

from __future__ import annotations

import math
from typing import Iterable


def two_sided_p(t: float) -> float | None:
    """Normal-approximation two-sided p-value for a t-statistic."""
    try:
        value = float(t)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value):
        return None
    return math.erfc(abs(value) / math.sqrt(2.0))


def bonferroni(p: float, n_trials: int) -> float:
    if n_trials <= 1:
        return min(1.0, p)
    return min(1.0, p * n_trials)


def classify_rho(ci_low: float | None, ci_high: float | None, hard: float = 0.9, warn: float = 0.7) -> str:
    """Classify a correlation interval using the frozen policy rule."""
    if ci_low is None or ci_high is None:
        return "warn"
    if ci_low > hard:
        return "fail"
    if ci_high < warn:
        return "pass"
    return "warn"


def block_bootstrap_mean(values: Iterable[float], block: int = 4, n_boot: int = 500, seed: int = 7):
    """Return (mean, ci_low, ci_high) for a short weekly series."""
    import numpy as np

    data = np.asarray([float(value) for value in values if value == value], dtype="float64")
    if len(data) == 0:
        return None, None, None
    mean = float(data.mean())
    if len(data) < block * 2:
        return mean, mean, mean
    rng = np.random.default_rng(seed)
    n_blocks = max(1, len(data) // block)
    means = []
    for _ in range(n_boot):
        picks = rng.integers(0, len(data) - block + 1, size=n_blocks)
        sample = np.concatenate([data[start : start + block] for start in picks])
        means.append(float(sample.mean()))
    return mean, float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))
