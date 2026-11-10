"""Adversarial review for Alpha DSL candidates."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from factor_lab.alpha.evaluation import _rank_corr, evaluate_factor


def _ic_values(factor: pd.DataFrame, forward: pd.DataFrame, step: int = 1) -> list[float]:
    values: list[float] = []
    for date in factor.index[::max(1, int(step))]:
        if date not in forward.index:
            continue
        pair = pd.concat([factor.loc[date].rename("f"), forward.loc[date].rename("r")], axis=1).dropna()
        if len(pair) >= 10:
            ic = _rank_corr(pair["f"], pair["r"])
            if np.isfinite(ic):
                values.append(float(ic))
    return values


def red_team_review(
    factor: pd.DataFrame,
    close: pd.DataFrame,
    metrics: dict[str, Any],
    *,
    horizon: int = 5,
    open_: pd.DataFrame | None = None,
    min_ic: float = 0.015,
    min_coverage: float = 0.6,
    max_turnover: float = 1.5,
    min_capacity: float = 3.0,
    permutations: int = 80,
    cost_bps: float = 20.0,
    seed: int = 7,
) -> dict[str, Any]:
    """Try to falsify a candidate before it can become a candidate."""
    findings: list[dict[str, Any]] = []
    checks: list[dict[str, Any]] = []
    if open_ is not None:
        forward = close.shift(-horizon) / open_.reindex(index=close.index, columns=close.columns).shift(-1) - 1.0
    else:
        forward = close.shift(-horizon) / close - 1.0
    factor = factor.reindex(index=close.index, columns=close.columns)
    actual_ic = float(metrics.get("ic_mean", float("nan")))

    def add(name: str, passed: bool, detail: Any) -> None:
        checks.append({"check": name, "passed": bool(passed), "detail": detail})
        if not passed:
            findings.append({"check": name, "detail": detail})

    add("coverage", float(metrics.get("coverage", 0.0)) >= min_coverage, metrics.get("coverage"))
    add("net_spread_positive", float(metrics.get("net_spread", -1.0)) > 0.0, metrics.get("net_spread"))
    add("turnover", float(metrics.get("turnover", 999.0)) <= max_turnover, metrics.get("turnover"))
    capacity = metrics.get("capacity_multiple")
    add("capacity", capacity is not None and float(capacity) >= min_capacity, capacity)
    add("ic_magnitude", np.isfinite(actual_ic) and abs(actual_ic) >= min_ic, actual_ic)
    w_net = float(metrics.get("winsorized_net_spread", float("nan")))
    t_net = float(metrics.get("trimmed_net_spread", float("nan")))
    add("tail_robustness", np.isfinite(w_net) and np.isfinite(t_net) and w_net > 0.0 and t_net > 0.0, {"winsorized": w_net, "trimmed": t_net})

    # Split-half sign consistency, evaluated independently.
    mid = len(factor.index) // 2
    halves = []
    for part in (factor.iloc[:mid], factor.iloc[mid:]):
        if len(part) < 40:
            continue
        try:
            result = evaluate_factor(
                part,
                close.loc[part.index],
                horizon=horizon,
                groups=5,
                cost_bps=cost_bps,
                amount=None,
                open_=open_,
            )
            halves.append(float(result.get("ic_mean", float("nan"))))
        except Exception:  # noqa: BLE001
            pass
    consistent = len(halves) == 2 and all(np.isfinite(halves)) and all(np.sign(halves) == np.sign(actual_ic))
    add("split_half_sign", consistent, halves)

    # Permutation test on the actual IC: shuffle forward returns cross-sectionally.
    rng = np.random.default_rng(seed)
    factor_ic = _ic_values(factor, forward, step=horizon)
    permutation_ics: list[float] = []
    forward_values = forward.to_numpy()
    for _ in range(max(20, int(permutations))):
        shuffled = forward_values.copy()
        for row in shuffled:
            rng.shuffle(row)
        shuffled_frame = pd.DataFrame(shuffled, index=forward.index, columns=forward.columns)
        ic_values = _ic_values(factor, shuffled_frame, step=horizon)
        if ic_values:
            permutation_ics.append(float(np.mean(ic_values)))
    if permutation_ics:
        p_value = (1.0 + sum(abs(value) >= abs(actual_ic) for value in permutation_ics)) / (1.0 + len(permutation_ics))
    else:
        p_value = 1.0
    add("permutation_p", p_value <= 0.10, {"p_value": p_value, "actual_ic": actual_ic, "n_permutations": len(permutation_ics)})
    add("ic_sample_size", len(factor_ic) >= 30, len(factor_ic))

    return {
        "passed": not findings,
        "findings": findings,
        "checks": checks,
        "permutation_p": p_value,
        "actual_ic": actual_ic,
        "split_half_ic": halves,
    }
