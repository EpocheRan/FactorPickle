"""Core factor_metrics tests: research calibration, Alpha golden, properties."""

from __future__ import annotations

import importlib.util
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

import factor_metrics as fm

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "metrics"


def _load_capture_module():
    path = FIXTURES / "capture_baseline.py"
    spec = importlib.util.spec_from_file_location("capture_baseline", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _diff(left, right, path: str = "", *, tol: float = 1e-12) -> list[tuple]:
    diffs: list[tuple] = []
    if isinstance(left, dict):
        if set(left) != set(right):
            diffs.append((path, "keys", sorted(set(left) ^ set(right))))
            return diffs
        for key in left:
            diffs.extend(_diff(left[key], right[key], f"{path}/{key}", tol=tol))
    elif isinstance(left, list):
        if len(left) != len(right):
            diffs.append((path, "len", len(left), len(right)))
            return diffs
        for index, (a, b) in enumerate(zip(left, right)):
            diffs.extend(_diff(a, b, f"{path}[{index}]", tol=tol))
    elif isinstance(left, (int, float)) and not isinstance(left, bool):
        x, y = float(left), float(right)
        if math.isnan(x) and math.isnan(y):
            return diffs
        if not (math.isfinite(x) and math.isfinite(y) and abs(x - y) <= tol):
            diffs.append((path, "number", left, right))
    else:
        if left != right:
            diffs.append((path, "value", left, right))
    return diffs


# ---------------------------------------------------------------- characterization
def test_research_report_output_is_unchanged() -> None:
    """The production report is the calibrated source of truth: zero drift."""
    capture = _load_capture_module()
    panel = capture.build_panel()
    factors = capture.build_factors(panel)
    current = {
        "reports": capture.capture_research(panel, factors),
        "complex_report": capture.capture_research_complex(panel, factors),
    }
    baseline = json.loads((FIXTURES / "research_baseline.json").read_text(encoding="utf-8"))
    for key in ("reports", "complex_report"):
        diffs = _diff(baseline[key], current[key])
        assert not diffs, f"{key} drifted from the frozen baseline: {diffs[:5]}"


def test_alpha_output_matches_a_full_golden() -> None:
    capture = _load_capture_module()
    panel = capture.build_panel()
    factors = capture.build_factors(panel)
    current = capture.capture_alpha(panel, factors)
    golden = json.loads((FIXTURES / "alpha_golden.json").read_text(encoding="utf-8"))["expressions"]
    diffs = _diff(golden, current)
    assert not diffs, f"Alpha metrics drifted: {diffs[:5]}"


# ---------------------------------------------------------------- primitive conventions
def _panel(days: int = 140, symbols: int = 40, seed: int = 5):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2021-01-04", periods=days)
    columns = [f"{600000 + i:06d}" for i in range(symbols)]
    rets = pd.DataFrame(rng.normal(0.0003, 0.015, (days, symbols)), index=dates, columns=columns)
    close = 10.0 * (1 + rets).cumprod()
    open_ = close * (1 + rng.normal(0, 0.003, (days, symbols)))
    return close, open_


def test_forward_returns_use_next_open_entry() -> None:
    close, open_ = _panel()
    forward = fm.forward_returns(close, open_, (5,))[5]
    date = close.index[10]
    expected = close.loc[close.index[15], :] / open_.loc[close.index[11], :] - 1.0
    assert np.allclose(forward.loc[date].to_numpy(), expected.to_numpy(), equal_nan=True)


def test_rank_ic_matches_per_date_spearman() -> None:
    close, open_ = _panel()
    forward = fm.forward_returns(close, open_, (5,))[5]
    factor = close.rank(axis=1)
    series = fm.rank_ic_frame(factor, forward, min_names=20)
    date = series.dropna().index[0]
    pair = pd.concat([factor.loc[date].rename("f"), forward.loc[date].rename("r")], axis=1).dropna()
    expected = float(pair["f"].rank().corr(pair["r"].rank()))
    assert series.loc[date] == expected or abs(float(series.loc[date]) - expected) < 1e-12


def test_running_newey_west_and_independent_t_match_manual() -> None:
    series = pd.Series(np.linspace(-0.01, 0.02, 60))
    t_indep, n_indep = fm.independent_t(series, 5)
    sampled = series.iloc[::5]
    manual = sampled.mean() / (sampled.std(ddof=1) / np.sqrt(len(sampled)))
    assert n_indep == len(sampled)
    assert abs(t_indep - manual) < 1e-12
    nw = fm.newey_west_t(series, lag=4)
    assert np.isfinite(nw)
    # lag=0 reduces to the plain t-statistic (population variance convention)
    values = series.to_numpy()
    demeaned = values - values.mean()
    var = (demeaned ** 2).sum() / len(values)
    plain = values.mean() / np.sqrt(var / len(values))
    assert abs(fm.newey_west_t(series, lag=0) - plain) < 1e-12


def test_cost_application_is_linear() -> None:
    close, open_ = _panel()
    factor = close.pct_change(10)
    no_cost = fm.evaluate_factor(factor, close, open_=open_, horizon=5, groups=5, cost_bps=0.0)
    with_cost = fm.evaluate_factor(factor, close, open_=open_, horizon=5, groups=5, cost_bps=20.0)
    base = no_cost.metrics
    net = with_cost.metrics
    assert abs((base["net_spread"] - net["net_spread"]) - net["cost_per_period"]) < 1e-5
    assert net["gross_spread"] == base["gross_spread"]
    assert with_cost.to_dict()["metrics_spec"]["cost"]["bps"] == 20.0


def test_monotonic_transform_leaves_ic_and_spread_intact() -> None:
    close, open_ = _panel()
    factor = close.pct_change(10)
    ranked = factor.rank(axis=1)
    a = fm.evaluate_factor(factor, close, open_=open_, horizon=5, groups=5, cost_bps=0.0).metrics
    b = fm.evaluate_factor(ranked, close, open_=open_, horizon=5, groups=5, cost_bps=0.0).metrics
    assert abs(a["ic_mean"] - b["ic_mean"]) < 1e-12
    assert abs(a["gross_spread"] - b["gross_spread"]) < 1e-12


def test_universe_mask_is_applied_to_coverage_and_sizing() -> None:
    close, open_ = _panel()
    factor = close.pct_change(10)
    universe = pd.DataFrame(True, index=close.index, columns=close.columns)
    universe[close.columns[0]] = False
    result = fm.evaluate_factor(
        factor, close, open_=open_, horizon=5, groups=5, cost_bps=0.0, universe=universe
    ).metrics
    expected_cells = int(universe.to_numpy().sum())
    covered = int((factor.notna() & universe).to_numpy().sum())
    assert abs(result["coverage"] - covered / expected_cells) < 1e-6
    assert abs(result["universe_size_avg"] - float(universe.sum(axis=1).mean())) < 1e-6


def test_trimmed_tail_extension_returns_group_means() -> None:
    close, open_ = _panel()
    forward = fm.forward_returns(close, open_, (5,))[5]
    factor = close.pct_change(20)
    trimmed = fm.trimmed_group_returns(factor, forward, groups=5, horizon=5)
    assert set(["Q1", "Q5", "spread"]).issubset(trimmed)
    assert np.isfinite(trimmed["spread"])


# ---------------------------------------------------------------- specs / audit
def test_profiles_and_spec_hash_are_stable() -> None:
    alpha = fm.ALPHA_GATE_SPEC
    research = fm.RESEARCH_SPEC
    assert alpha.sampling == "non_overlapping" and research.sampling == "daily"
    assert alpha.t_method == "plain" and research.t_method == "newey_west"
    assert alpha.config_hash() == alpha.config_hash()
    assert alpha.config_hash() != research.config_hash()
    payload = alpha.to_dict()
    assert payload["cost"]["convention"] == "two_leg"
    assert payload["cost"]["bps_meaning"] == "per_side"
    assert research.to_dict()["cost"]["bps_meaning"] == "round_trip"
    assert payload["capacity"]["formula"] == "research_adv"

def _group_members(factor: pd.DataFrame, groups: int, horizon: int) -> list[dict]:
    """Replicate the calibrated grouping schedule to audit the cost formula."""
    positions = list(range(0, max(0, len(factor.index) - horizon - 1), horizon))
    members = []
    for position in positions:
        row = factor.iloc[position]
        valid = row.dropna()
        if len(valid) < groups * 5:
            continue
        ranked = valid.rank(method="first")
        buckets = pd.qcut(ranked, groups, labels=False, duplicates="drop")
        members.append({g: set(valid.index[buckets == g]) for g in range(groups)})
    return members


def test_round_trip_cost_equals_independent_buy_sell_accounting() -> None:
    """Semantic proof of the cost formula.

    ``cost_bps`` is a **round-trip (buy+sell) fee**.  For equal-weight groups
    with constant size, ``turnover_g`` (research definition) equals the one-way
    turnover; buying the replacements and selling the leavers at half the
    round-trip fee each must equal ``turnover_g × cost_bps``.  The opening
    period is excluded because it charges one-way initialization at the same
    round-trip rate (conservative).
    """
    close, open_ = _panel(days=200, symbols=60)
    factor = close.pct_change(10)
    horizon = 5
    bps = 20.0
    quantile = fm.quantile_nav(factor, close, open_, groups=5, horizon=horizon, cost_bps=bps)
    turnover_df = quantile.turnover_df
    members = _group_members(factor, 5, horizon)
    assert len(members) == len(turnover_df)
    for row_index in range(1, len(members)):
        period = turnover_df.iloc[row_index]
        for g in range(5):
            current = members[row_index][g]
            previous = members[row_index - 1][g]
            buy_notional = len(current - previous) / max(1, len(current))
            sell_notional = len(previous - current) / max(1, len(previous))
            independent = (buy_notional + sell_notional) * (bps / 2.0) / 10000.0
            charged = float(period[f"Q{g + 1}"]) * bps / 10000.0
            assert abs(independent - charged) < 1e-12


def test_alpha_per_side_cost_is_double_the_round_trip_formula() -> None:
    close, open_ = _panel(days=160, symbols=60)
    factor = close.pct_change(10)
    per_side = fm.evaluate_factor(factor, close, open_=open_, horizon=5, groups=5, cost_bps=20.0)
    round_trip_spec = fm.ALPHA_GATE_SPEC.replace(
        cost=fm.CostModel(bps=20.0, convention="two_leg", bps_meaning="round_trip")
    )
    round_trip = fm.evaluate_factor(
        factor, close, open_=open_, horizon=5, groups=5, cost_bps=20.0, spec=round_trip_spec
    )
    assert per_side.metrics["gross_spread"] == round_trip.metrics["gross_spread"]
    assert abs(per_side.metrics["cost_per_period"] - 2.0 * round_trip.metrics["cost_per_period"]) < 1e-10
    assert abs(
        (round_trip.metrics["net_spread"] - per_side.metrics["net_spread"])
        - round_trip.metrics["cost_per_period"]
    ) < 1e-6

