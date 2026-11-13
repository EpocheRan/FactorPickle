"""Mandate policy and long-only promotion Gate."""

from __future__ import annotations

import numpy as np
import pandas as pd

from factor_lab.alpha.compiler import AlphaContext
from factor_lab.alpha.gates import alpha_gate_spec, long_only_gate
from factor_lab.alpha.mandate import data_capability_blockers, load_mandate
from factor_lab.alpha.universe import apply_size_screen


def test_mandate_is_long_only_20d_wider_pool_and_1m_aum() -> None:
    mandate = load_mandate()
    assert mandate.deployment_mode == "long_only"
    assert mandate.primary_horizon == 20
    assert mandate.universe_top_n == 1000
    assert mandate.size_screen_fraction == 0.30
    assert mandate.aum_cny == 1_000_000.0
    assert mandate.benchmark_primary == "ew_eligible_pool"
    assert "delisting_adjusted_universe" in data_capability_blockers(mandate)
    assert alpha_gate_spec().capacity.target_aum == 1_000_000.0


def test_long_only_gate_passes_only_on_deployable_metrics() -> None:
    metrics = {
        "n_dates": 100,
        "long_only_net_excess_annualized": 0.06,
        "long_only_t": 2.5,
        "long_only_te_annualized": 0.05,
        "turnover": 0.375,
        "long_only_gross_excess_per_period": 0.0060,
        "cost_per_period": 0.00037,
        "capacity_multiple": 50.0,
    }
    assert long_only_gate(metrics)["verdict"] == "pass"
    # A raw turnover cap is no longer a veto; cost budget is.
    assert long_only_gate(dict(metrics, turnover=0.50))["verdict"] == "pass"
    high_cost = dict(metrics, cost_per_period=0.0025)
    assert long_only_gate(high_cost)["verdict"] == "fail"
    low_capacity = dict(metrics, capacity_multiple=1.0)
    assert long_only_gate(low_capacity)["verdict"] == "fail"
    low_t = dict(metrics, long_only_t=1.0)
    assert long_only_gate(low_t)["verdict"] == "fail"
    missing = {"n_dates": 100, "turnover": 0.2}
    assert long_only_gate(missing)["verdict"] == "insufficient_evidence"


def test_size_screen_excludes_smallest_float_cap() -> None:
    dates = pd.bdate_range("2023-01-02", periods=5)
    symbols = [f"{600000 + index:06d}" for index in range(10)]
    float_mv = pd.DataFrame(np.arange(1, 51, dtype=float).reshape(5, 10), index=dates, columns=symbols)
    context = AlphaContext.from_fields({"close": float_mv, "float_mv": float_mv}, close=float_mv)
    mask = pd.DataFrame(True, index=dates, columns=symbols)
    screened = apply_size_screen(context, mask, exclude_smallest_fraction=0.30)
    assert screened.iloc[0].sum() == 7
    assert screened.iloc[0].iloc[:3].sum() == 0


def test_research_evidence_is_long_only_primary() -> None:
    mandate = load_mandate()
    assert mandate.research_evidence["primary"] == "long_only"
    assert mandate.research_evidence["long_short_role"] == "diagnostic"
    assert mandate.research_evidence["promotion"]["ls_is_veto"] is False


def test_long_only_gate_rejects_low_coverage() -> None:
    metrics = {
        "n_dates": 100,
        "long_only_net_excess_annualized": 0.06,
        "long_only_t": 2.5,
        "long_only_te_annualized": 0.05,
        "turnover": 0.30,
        "long_only_gross_excess_per_period": 0.0060,
        "cost_per_period": 0.00037,
        "capacity_multiple": 50.0,
        "coverage": 0.80,
    }
    result = long_only_gate(metrics)
    assert result["verdict"] == "fail"
    assert any("coverage" in reason for reason in result["reasons"])
