"""Weak-signal ensemble construction and power diagnostics.

The design goal is to stop promoting the single largest t-stat.  A fixed,
pre-registered set of weak positive signals is rank-combined with equal weights;
the object under test is the ensemble, not each component.  This module does not
select components, tune weights or flip signs after seeing results.
"""

from __future__ import annotations

import math
from statistics import NormalDist
from typing import Any, Iterable, Mapping

import numpy as np
import pandas as pd

from factor_lab.alpha.compiler import compile_expression
from factor_lab.alpha.deployability import evaluate_long_only
from factor_lab.alpha.gates import alpha_gate_spec, long_only_gate
from factor_lab.alpha.mandate import load_mandate


class WeakSignalError(ValueError):
    pass


def _rank(frame: pd.DataFrame) -> pd.DataFrame:
    return frame.rank(axis=1, pct=True)


def build_rank_composite(
    components: Mapping[str, str],
    context: Any,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Compile and equal-weight rank-combine frozen component expressions."""
    if len(components) < 3:
        raise WeakSignalError("weak-signal ensemble requires at least 3 components")
    frames = {}
    for name, expression in components.items():
        frame, _report = compile_expression(str(expression), context)
        frames[str(name)] = _rank(frame)
    # All component ranks are already oriented to the positive direction.
    composite = sum(frames.values()) / float(len(frames))
    corr = {}
    names = list(frames)
    for i, left in enumerate(names):
        for right in names[i + 1 :]:
            pair = frames[left].corrwith(frames[right], axis=1).dropna()
            corr[f"{left}|{right}"] = float(pair.mean()) if len(pair) else None
    values = [value for value in corr.values() if value is not None]
    return composite, {
        "schema": "factor_lab.alpha.weak_signal_composite.v1",
        "components": list(components),
        "weights": {name: 1.0 / len(components) for name in components},
        "pairwise_rank_correlation": corr,
        "mean_pairwise_correlation": float(np.mean(values)) if values else None,
        "max_pairwise_correlation": float(max(values)) if values else None,
        "min_pairwise_correlation": float(min(values)) if values else None,
    }


def _critical_t(alpha: float = 0.05, two_sided: bool = True) -> float:
    if two_sided:
        return float(NormalDist().inv_cdf(1.0 - alpha / 2.0))
    return float(NormalDist().inv_cdf(1.0 - alpha))


def ensemble_power(
    component_t_stats: Mapping[str, float],
    *,
    n_periods: int,
    mean_correlation: float | None = None,
    alpha: float = 0.05,
) -> dict[str, Any]:
    """Approximate ensemble power from component t-stats and correlation.

    Each component IR is approximated as t / sqrt(n_periods).  For equal weights
    and average pairwise correlation rho, the ensemble IR is
    mean(IR) * sqrt(N / (1 + (N - 1) * rho)).  This is a design-time power
    diagnostic, not a substitute for the actual composite backtest.
    """
    t_values = [float(value) for value in component_t_stats.values() if value is not None and math.isfinite(float(value))]
    if not t_values or n_periods < 2:
        return {"schema": "factor_lab.alpha.ensemble_power.v1", "available": False, "reason": "insufficient component t-stats/periods"}
    n = len(t_values)
    rho = float(mean_correlation or 0.0)
    rho = max(-1.0 / (n - 1) + 1e-6, min(1.0, rho))
    mean_t = float(np.mean(t_values))
    ir_single = mean_t / math.sqrt(float(n_periods))
    ensemble_ir = ir_single * math.sqrt(n / (1.0 + (n - 1.0) * rho))
    ensemble_t = ensemble_ir * math.sqrt(float(n_periods))
    critical = _critical_t(alpha)
    power = 1.0 - NormalDist().cdf(critical - ensemble_t) + NormalDist().cdf(-critical - ensemble_t)
    mde_t = critical / math.sqrt(float(n_periods))  # per-period excess needed at this n
    return {
        "schema": "factor_lab.alpha.ensemble_power.v1",
        "available": True,
        "n_components": n,
        "n_periods": int(n_periods),
        "mean_component_t": mean_t,
        "mean_pairwise_rank_correlation": rho,
        "ir_single_approx": ir_single,
        "ensemble_ir_approx": ensemble_ir,
        "ensemble_t_approx": ensemble_t,
        "critical_t": critical,
        "power_approx": max(0.0, min(1.0, power)),
        "mde_t": mde_t,
        "note": "Approximate design-time power; the realized composite t comes from the deterministic evaluator.",
    }


def evaluate_ensemble(
    composite: pd.DataFrame,
    context: Any,
    *,
    start: str,
    end: str,
    universe: pd.DataFrame,
    size_universe: pd.DataFrame,
    horizons: Iterable[int] = (5, 20, 60),
    windows: Mapping[str, tuple[str, str]] | None = None,
    component_t_stats: Mapping[str, float] | None = None,
    mean_correlation: float | None = None,
) -> dict[str, Any]:
    mandate = load_mandate()
    spec = alpha_gate_spec()
    open_ = context.fields.get("open")
    full: dict[str, Any] = {}
    size: dict[str, Any] = {}
    for horizon in horizons:
        full[str(horizon)] = evaluate_long_only(
            composite.loc[pd.Timestamp(start) : pd.Timestamp(end)],
            context.close.loc[pd.Timestamp(start) : pd.Timestamp(end)],
            open_=open_.loc[pd.Timestamp(start) : pd.Timestamp(end)] if open_ is not None else None,
            horizon=int(horizon),
            cost_bps=20.0,
            universe=universe.loc[pd.Timestamp(start) : pd.Timestamp(end)],
            spec=spec,
        )
        size[str(horizon)] = evaluate_long_only(
            composite.loc[pd.Timestamp(start) : pd.Timestamp(end)],
            context.close.loc[pd.Timestamp(start) : pd.Timestamp(end)],
            open_=open_.loc[pd.Timestamp(start) : pd.Timestamp(end)] if open_ is not None else None,
            horizon=int(horizon),
            cost_bps=20.0,
            universe=size_universe.loc[pd.Timestamp(start) : pd.Timestamp(end)],
            spec=spec,
        )
    regime = {
        name: evaluate_long_only(
            composite.loc[pd.Timestamp(window_start) : pd.Timestamp(window_end)],
            context.close.loc[pd.Timestamp(window_start) : pd.Timestamp(window_end)],
            open_=open_.loc[pd.Timestamp(window_start) : pd.Timestamp(window_end)] if open_ is not None else None,
            horizon=20,
            cost_bps=20.0,
            universe=size_universe.loc[pd.Timestamp(window_start) : pd.Timestamp(window_end)],
            spec=spec,
        )
        for name, (window_start, window_end) in (windows or {}).items()
    }
    promotion = long_only_gate(size["20"], mandate)
    power = ensemble_power(
        component_t_stats or {},
        n_periods=int(size["20"].get("n_dates") or 0),
        mean_correlation=mean_correlation,
        alpha=0.05,
    )
    return {
        "schema": "factor_lab.alpha.weak_signal_ensemble_result.v1",
        "evaluation_start": start,
        "evaluation_end": end,
        "long_only": {"full": full, "size": size},
        "regime_20d": regime,
        "promotion": promotion,
        "power": power,
    }
