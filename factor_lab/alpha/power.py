"""Campaign power / minimum detectable effect diagnostics.

This is deliberately a planning tool, not a gate: it makes the cost of a
multiple-testing threshold explicit before another campaign is opened.
"""

from __future__ import annotations

import math
from statistics import NormalDist
from typing import Any


def analyze_power(
    *,
    n_periods: int,
    n_trials: int,
    n_eff: int | None = None,
    q: float = 0.10,
    horizon: int = 5,
    annual_periods: float = 252.0,
    observed_t: float | None = None,
    sigma_period: float | None = None,
) -> dict[str, Any]:
    n_eff = int(n_eff or n_trials)
    if n_periods < 3 or n_eff < 1:
        raise ValueError("power requires n_periods>=3 and n_eff>=1")
    rank1_p = float(q) / float(n_eff)
    # one-sided normal approximation used by the FDR threshold display
    z = NormalDist().inv_cdf(1.0 - rank1_p / 2.0)
    se = (sigma_period / math.sqrt(n_periods)) if sigma_period else 1.0
    mde = z * se
    annual = (1.0 + mde) ** (annual_periods / max(1.0, float(horizon))) - 1.0 if sigma_period is None else mde * annual_periods / max(1.0, float(horizon))
    result = {
        "schema": "factor_lab.alpha.power.v1",
        "n_periods": int(n_periods),
        "n_trials": int(n_trials),
        "n_eff": int(n_eff),
        "q": float(q),
        "rank1_p": rank1_p,
        "rank1_z": z,
        "horizon": int(horizon),
        "sigma_period": sigma_period,
        "mde_spread_period": mde if sigma_period else None,
        "mde_annual_simple": annual if sigma_period else None,
        "observed_t": observed_t,
    }
    if observed_t is not None:
        result["pass_probability_under_observed_effect"] = float(1.0 - NormalDist().cdf(z - abs(float(observed_t))))
    return result


def budget_summary(
    *,
    attempts: int,
    unique: int,
    n_eff: int,
    periods: int,
    q: float = 0.10,
    observed_t: float | None = None,
) -> dict[str, Any]:
    base = analyze_power(
        n_periods=periods,
        n_trials=attempts,
        n_eff=n_eff,
        q=q,
        observed_t=observed_t,
    )
    base.update({"attempts": int(attempts), "unique": int(unique), "schema": "factor_lab.alpha.power_budget.v1"})
    return base
