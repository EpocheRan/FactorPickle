"""Cross-engine parity: research is the calibrated source of truth.

These tests pin the A-full contract:

1. The production report re-exports the *same* callables from ``factor_metrics``
   (one implementation, not a copy).
2. When the Alpha profile is pointed at the same spec as research, the assembled
   IC/spread/turnover statistics agree with the report's primitives.
3. Intentional profile differences (sampling, t-method) stay explicit.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

import factor_metrics as fm
import research.report.metrics as research_metrics


MOVED_NAMES = [
    "forward_returns",
    "newey_west_t",
    "independent_t",
    "block_bootstrap_ci",
    "_rank_ic_frame",
    "_spearman_ic",
    "_winsorize",
    "_winsorize_frame",
    "_winsor_group_returns",
    "tradeable_mask",
    "_limit_matrix",
    "board_limit_pct",
    "_quantile_nav",
    "QuantileResult",
    "neutralize_panel",
    "neutralized_ic",
    "neutralize_factor",
    "_stratified_ic",
    "_regime_table",
    "_capacity",
    "_filter_test",
    "_filter_test_side",
    "_incremental",
    "_cost_sensitivity",
    "_ls_stats",
    "_period_performance",
    "_performance",
    "_annual_returns",
    "_tradeability",
    "_sell_side_summary",
    "_delayed_exit",
]


def _panel(days: int = 200, symbols: int = 60, seed: int = 11):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2021-01-04", periods=days)
    columns = [f"{600000 + i:06d}" for i in range(symbols)]
    rets = pd.DataFrame(rng.normal(0.0004, 0.016, (days, symbols)), index=dates, columns=columns)
    close = 10.0 * (1 + rets).cumprod()
    open_ = close * (1 + rng.normal(0, 0.004, (days, symbols)))
    return close, open_


def test_report_module_reuses_core_callables() -> None:
    checked = 0
    for name in MOVED_NAMES:
        if not hasattr(research_metrics, name):
            continue
        assert getattr(research_metrics, name) is getattr(fm, name), f"{name} is not the core implementation"
        checked += 1
    assert checked >= 15, f"too few shared callables: {checked}"
    import research.report.diagnostics as diagnostics

    assert diagnostics.spread_stats is fm.spread_stats
    assert diagnostics.group_monotonicity is fm.group_monotonicity


def test_alpha_ic_matches_research_formula_on_the_same_schedule() -> None:
    close, open_ = _panel()
    factor = close.rolling(20, min_periods=10).mean() / close - 1.0
    forward = fm.forward_returns(close, open_, (5,))[5]
    horizon = 5
    schedule = close.index[list(range(0, max(0, len(close) - horizon - 1), horizon))]
    expected = fm.rank_ic_frame(factor.loc[schedule], forward.loc[schedule], min_names=10).mean()
    result = fm.evaluate_factor(factor, close, open_=open_, horizon=5, groups=5, cost_bps=20.0)
    assert abs(result.metrics["ic_mean"] - float(expected)) < 5e-7


def test_daily_profile_reproduces_research_ic() -> None:
    close, open_ = _panel()
    factor = close.rolling(20, min_periods=10).mean() / close - 1.0
    forward = fm.forward_returns(close, open_, (5,))[5]
    expected = fm.rank_ic_frame(factor, forward, min_names=20).mean()
    result = fm.evaluate_factor(
        factor,
        close,
        open_=open_,
        horizon=5,
        groups=5,
        cost_bps=20.0,
        spec=fm.RESEARCH_SPEC.replace(min_ic_names=20),
    )
    assert abs(result.metrics["ic_mean"] - float(expected)) < 5e-7


def test_research_report_spread_comes_from_core_quantile_engine() -> None:
    close, open_ = _panel()
    factor = close.rolling(20, min_periods=10).mean() / close - 1.0
    report = research_metrics.build_report(
        factor,
        close,
        open_,
        factor_id="parity",
        category="test",
        direction=1,
        universe="test",
        horizons=(5,),
        primary_horizon=5,
        groups=5,
        cost_bps=20.0,
        full=False,
        sections=("definition",),
    )
    counts = factor.notna().sum(axis=1)
    usable = counts[counts >= max(30, int(0.2 * factor.shape[1]))]
    effective_start = usable.index.min()
    trimmed_factor = factor.loc[factor.index >= effective_start]
    trimmed_close = close.loc[close.index >= effective_start]
    trimmed_open = open_.loc[open_.index >= effective_start]
    quantile = fm.quantile_nav(
        trimmed_factor, trimmed_close, trimmed_open, groups=5, horizon=5, cost_bps=20.0
    )
    periods_gross = quantile.periods_gross
    gross = periods_gross["Q5"] - periods_gross["Q1"]
    assert abs(report.spread_gross["mean"] - float(gross.mean())) < 1e-12


def test_intentional_profile_differences_are_explicit() -> None:
    assert fm.ALPHA_GATE_SPEC.entry == fm.RESEARCH_SPEC.entry == "next_open"
    assert fm.ALPHA_GATE_SPEC.sampling == "non_overlapping"
    assert fm.ALPHA_GATE_SPEC.t_method == "plain"
    assert fm.RESEARCH_SPEC.sampling == "daily"
    assert fm.RESEARCH_SPEC.t_method == "newey_west"
    # Cost semantics are explicit profile configuration:
    assert fm.ALPHA_GATE_SPEC.cost.bps_meaning == "per_side"
    assert fm.RESEARCH_SPEC.cost.bps_meaning == "round_trip"
