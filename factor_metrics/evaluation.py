"""Cross-sectional evaluation assembled from the research-calibrated primitives.

Every formula in this module is taken from the production factor report
(``research.report.metrics``) and lives in this core package; the Alpha gate and
any future product only choose a :class:`~factor_metrics.spec.MetricsSpec`
profile.  The only product-specific additions here are:

- the PIT ``universe`` mask (rows are nulled before the calibrated engine runs),
- the non-overlapping schedule (for the Alpha profile),
- the trimmed-tail diagnostic (research provides a winsorised variant only).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .capacity import _capacity
from .ic import _rank_ic_frame
from .performance import _ls_stats, _period_performance
from .quantile import _quantile_nav
from .result import FactorMetrics
from .returns import forward_returns
from .spec import ALPHA_GATE_SPEC, CostModel, MetricsSpec
from .stats import (
    TRADING_DAYS,
    group_monotonicity,
    independent_t,
    newey_west_t,
    spread_stats,
)
from .winsor import _trimmed_group_returns, _winsorize_frame


def _rebalance_positions(n_dates: int, horizon: int) -> list[int]:
    """Same schedule as the calibrated ``_quantile_nav`` loop."""
    return list(range(0, max(0, int(n_dates) - int(horizon) - 1), int(horizon)))


def _forward_matrix(
    close: pd.DataFrame,
    open_: pd.DataFrame | None,
    horizon: int,
    entry: str,
) -> pd.DataFrame:
    if entry == "next_open":
        if open_ is None:
            raise ValueError("next_open entry requires an open matrix")
        return forward_returns(close, open_, (int(horizon),))[int(horizon)]
    return close.shift(-int(horizon)) / close - 1.0


def _entry_frame(close: pd.DataFrame, open_: pd.DataFrame | None, entry: str) -> pd.DataFrame:
    if entry == "next_open":
        if open_ is None:
            raise ValueError("next_open entry requires an open matrix")
        return open_
    # Calibrated engine buys at open[i+1]; shifting close by 1 reproduces the
    # historical same-close Alpha convention (entry at close[i]).
    return close.shift(1)


def _t_stat(series: pd.Series, spec: MetricsSpec) -> float:
    clean = series.replace([np.inf, -np.inf], np.nan).dropna()
    if len(clean) == 0:
        return float("nan")
    if spec.t_method == "newey_west":
        lag = spec.nw_lag if spec.nw_lag is not None else max(1, int(spec.horizon) - 1)
        return float(newey_west_t(clean, lag=int(lag)))
    if spec.t_method == "none":
        return float("nan")
    value, _ = independent_t(clean, 1)
    return float(value)


def _round(value: float, digits: int) -> float:
    number = float(value)
    return round(number, digits) if np.isfinite(number) else float("nan")


def _effective_spec(
    spec: MetricsSpec | None,
    *,
    horizon: int,
    groups: int,
    cost_bps: float,
    min_ic_names: int | None,
) -> MetricsSpec:
    base = spec or ALPHA_GATE_SPEC
    changes: dict = {
        "horizon": int(horizon),
        "groups": int(groups),
        "cost": CostModel(
            bps=float(cost_bps),
            convention=base.cost.convention,
            bps_meaning=base.cost.bps_meaning,
        ),
    }
    if min_ic_names is not None:
        changes["min_ic_names"] = int(min_ic_names)
    return base.replace(**changes)


def _schedule(index: pd.DatetimeIndex, spec: MetricsSpec) -> pd.DatetimeIndex:
    if spec.sampling == "daily":
        return index
    positions = _rebalance_positions(len(index), spec.horizon)
    return index[positions]


def _ic_series(
    factor: pd.DataFrame, forward: pd.DataFrame, spec: MetricsSpec
) -> pd.Series:
    schedule = _schedule(factor.index, spec)
    if len(schedule) == 0:
        return pd.Series(dtype="float64")
    sub_factor = factor.loc[schedule]
    sub_forward = forward.loc[schedule]
    series = _rank_ic_frame(sub_factor, sub_forward, min_names=int(spec.min_ic_names))
    return series.replace([np.inf, -np.inf], np.nan).dropna()


def _spread_series(
    quantile_result, groups: int, cost_model: CostModel
) -> tuple[pd.Series, pd.Series, pd.Series, pd.Series]:
    """Return (gross, leg_cost, net, leg_turnover) period series."""
    _, turnover_df, _, periods_gross, _, _, _ = quantile_result
    q_hi, q_lo = f"Q{groups}", "Q1"
    if periods_gross.empty or q_hi not in periods_gross or q_lo not in periods_gross:
        empty = pd.Series(dtype="float64")
        return empty, empty, empty, empty
    gross = (periods_gross[q_hi] - periods_gross[q_lo]).dropna()
    legs = (
        turnover_df.get(q_hi, pd.Series(dtype="float64")).reindex(gross.index).fillna(0.0)
        + turnover_df.get(q_lo, pd.Series(dtype="float64")).reindex(gross.index).fillna(0.0)
    )
    cost = legs * float(cost_model.bps) * cost_model.one_way_cost_multiplier() / 10000.0
    net = gross - cost
    return gross, cost, net, legs


def evaluate_factor(
    factor: pd.DataFrame,
    close: pd.DataFrame,
    *,
    open_: pd.DataFrame | None = None,
    amount: pd.DataFrame | None = None,
    horizon: int = 5,
    groups: int = 5,
    cost_bps: float = 20.0,
    universe: pd.DataFrame | None = None,
    spec: MetricsSpec | None = None,
    min_ic_names: int | None = None,
) -> FactorMetrics:
    """Evaluate one factor with the calibrated research primitives.

    ``universe`` is an optional PIT mask; masked cells are dropped before the
    calibrated grouping/IC engine runs, so no new formula is introduced.
    """

    spec = _effective_spec(
        spec, horizon=horizon, groups=groups, cost_bps=cost_bps, min_ic_names=min_ic_names
    )
    groups = int(spec.groups)
    horizon = int(spec.horizon)
    entry = spec.entry if open_ is not None else "same_close"
    if entry == "next_open":
        spec = spec.replace(entry="next_open")
    else:
        spec = spec.replace(entry="same_close")

    factor = factor.reindex(index=close.index, columns=close.columns).astype(float)
    raw_factor = factor.copy()
    universe_mask = None
    if universe is not None:
        universe_mask = (
            universe.reindex(index=factor.index, columns=factor.columns)
            .fillna(False)
            .astype(bool)
        )
        factor = factor.where(universe_mask)

    forward = _forward_matrix(close, open_, horizon, entry)
    entry_frame = _entry_frame(close, open_, entry)
    quantile = _quantile_nav(
        factor, close, entry_frame, groups=groups, horizon=horizon, cost_bps=float(cost_bps)
    )
    gross_series, cost_series, net_series, leg_turnover = _spread_series(
        quantile, groups, spec.cost
    )
    if gross_series.empty:
        metrics = _empty_metrics(spec, factor, raw_factor, universe_mask, close)
        return FactorMetrics(spec=spec, metrics=metrics)

    ic_series = _ic_series(factor, forward, spec)
    ic_mean = float(ic_series.mean()) if len(ic_series) else float("nan")
    ic_std = float(ic_series.std()) if len(ic_series) > 1 else float("nan")
    icir = (
        float(ic_mean / ic_std * np.sqrt(TRADING_DAYS / horizon))
        if np.isfinite(ic_std) and ic_std > 0
        else float("nan")
    )
    ic_t = _t_stat(ic_series, spec)
    ic_win = float((ic_series > 0).mean()) if len(ic_series) else float("nan")

    q_hi, q_lo = f"Q{groups}", "Q1"
    periods_gross = quantile[3]
    group_frame = periods_gross.mean()
    group_means = [float(group_frame.get(f"Q{i + 1}", np.nan)) for i in range(groups)]
    monotonicity = group_monotonicity(group_frame)[0]

    gross_spread = float(gross_series.mean())
    net_spread = float(net_series.mean())
    gross_stats = spread_stats(gross_series)
    net_stats = spread_stats(net_series)
    cost_per_period = float(cost_series.mean())
    turnover = float(leg_turnover.mean() / 2.0) if len(leg_turnover) else float("nan")
    sharpe = float(_period_performance(net_series, horizon).get("sharpe", np.nan))

    winsor_net = _winsorized_net_spread(
        factor, close, entry_frame, groups, horizon, cost_bps, spec
    )
    trimmed = _trimmed_group_returns(
        factor,
        forward,
        groups=groups,
        horizon=horizon,
        lower=float(spec.trim[0]),
        upper=float(spec.trim[1]),
    )
    trimmed_spread = float(trimmed.get("spread", np.nan))
    trimmed_net = (
        trimmed_spread - cost_per_period if np.isfinite(trimmed_spread) else float("nan")
    )

    capacity_multiple = None
    if amount is not None:
        capacity = _capacity(amount, factor, horizon, groups)
        aum_est = capacity.get("aum_est") if isinstance(capacity, dict) else None
        if aum_est is not None and np.isfinite(float(aum_est)):
            capacity_multiple = float(aum_est) / float(spec.capacity.target_aum)

    coverage, universe_size_avg = _coverage(raw_factor, universe_mask)
    metrics = {
        "n_dates": int(len(periods_gross)),
        "n_symbols": int(close.shape[1]),
        "coverage": _round(coverage, 6),
        "ic_mean": _round(ic_mean, 6),
        "ic_std": _round(ic_std, 6),
        "icir": _round(icir, 6),
        "ic_t": _round(ic_t, 4),
        "ic_win": _round(ic_win, 4),
        "gross_spread": _round(gross_spread, 6),
        "gross_t": _round(gross_stats.get("t", np.nan), 4),
        "net_spread": _round(net_spread, 6),
        "net_t": _round(net_stats.get("t", np.nan), 4),
        "turnover": _round(turnover, 6),
        "cost_per_period": _round(cost_per_period, 6),
        "sharpe": _round(sharpe, 4),
        "monotonicity": _round(monotonicity, 4),
        "group_means": [_round(value, 6) for value in group_means],
        "capacity_multiple": (
            None if capacity_multiple is None else _round(capacity_multiple, 4)
        ),
        "capacity_target_aum": float(spec.capacity.target_aum),
        "capacity_participation": float(spec.capacity.participation),
        "winsorized_factor_net_spread": _round(winsor_net, 6),
        "extras": {
            "trimmed_net_spread": _round(trimmed_net, 6),
            "trimmed_net_spread_experimental": True,
        },
        "universe_size_avg": (
            None if universe_size_avg is None else _round(universe_size_avg, 4)
        ),
        "horizon": horizon,
        "rebalance": horizon,
        "entry_rule": entry,
        "groups": groups,
        "cost_bps": float(cost_bps),
    }
    return FactorMetrics(spec=spec, metrics=metrics)


def _winsorized_net_spread(
    factor: pd.DataFrame,
    close: pd.DataFrame,
    entry_frame: pd.DataFrame,
    groups: int,
    horizon: int,
    cost_bps: float,
    spec: MetricsSpec,
) -> float:
    try:
        winsor_factor = _winsorize_frame(
            factor, float(spec.winsorize[0]), float(spec.winsorize[1])
        )
        quantile = _quantile_nav(
            winsor_factor,
            close,
            entry_frame,
            groups=groups,
            horizon=horizon,
            cost_bps=float(cost_bps),
        )
        _, _, net_series, _ = _spread_series(quantile, groups, spec.cost)
        return float(net_series.mean()) if len(net_series) else float("nan")
    except Exception:  # noqa: BLE001 - diagnostic must never break the evaluation
        return float("nan")


def _coverage(
    raw_factor: pd.DataFrame, universe_mask: pd.DataFrame | None
) -> tuple[float, float | None]:
    if universe_mask is not None:
        cells = int(universe_mask.to_numpy().sum())
        covered = int((raw_factor.notna() & universe_mask).to_numpy().sum())
        coverage = covered / cells if cells else float("nan")
        size = float(universe_mask.sum(axis=1).mean()) if len(universe_mask) else 0.0
        return coverage, size
    return float(raw_factor.notna().sum().sum() / max(1, raw_factor.size)), None


def _empty_metrics(
    spec: MetricsSpec,
    factor: pd.DataFrame,
    raw_factor: pd.DataFrame,
    universe_mask: pd.DataFrame | None,
    close: pd.DataFrame,
) -> dict:
    nan = float("nan")
    coverage, universe_size_avg = _coverage(raw_factor, universe_mask)
    return {
        "n_dates": 0,
        "n_symbols": int(close.shape[1]),
        "coverage": _round(coverage, 6),
        "ic_mean": nan,
        "ic_std": nan,
        "icir": nan,
        "ic_t": nan,
        "ic_win": nan,
        "gross_spread": nan,
        "gross_t": nan,
        "net_spread": nan,
        "net_t": nan,
        "turnover": nan,
        "cost_per_period": nan,
        "sharpe": nan,
        "monotonicity": nan,
        "group_means": [nan] * int(spec.groups),
        "capacity_multiple": None,
        "capacity_target_aum": float(spec.capacity.target_aum),
        "capacity_participation": float(spec.capacity.participation),
        "winsorized_factor_net_spread": nan,
        "extras": {
            "trimmed_net_spread": nan,
            "trimmed_net_spread_experimental": True,
        },
        "universe_size_avg": (
            None if universe_size_avg is None else _round(universe_size_avg, 4)
        ),
        "horizon": int(spec.horizon),
        "rebalance": int(spec.horizon),
        "entry_rule": spec.entry,
        "groups": int(spec.groups),
        "cost_bps": float(spec.cost.bps),
    }


def evaluate_long_only(
    factor: pd.DataFrame,
    close: pd.DataFrame,
    *,
    open_: pd.DataFrame | None = None,
    horizon: int = 5,
    cost_bps: float = 20.0,
    top_fraction: float = 0.20,
    min_names: int = 10,
    universe: pd.DataFrame | None = None,
    spec: MetricsSpec | None = None,
) -> dict:
    """Long-only lens; statistics follow the calibrated ``_ls_stats`` formulas."""
    spec = (spec or ALPHA_GATE_SPEC).replace(
        horizon=int(horizon), cost=CostModel(bps=float(cost_bps), convention="two_leg")
    )
    entry = "next_open" if open_ is not None else "same_close"
    factor = factor.reindex(index=close.index, columns=close.columns).astype(float)
    universe_mask = None
    if universe is not None:
        universe_mask = (
            universe.reindex(index=factor.index, columns=factor.columns)
            .fillna(False)
            .astype(bool)
        )
        factor = factor.where(universe_mask)
    forward = _forward_matrix(close, open_, int(horizon), entry)
    positions = _rebalance_positions(len(factor.index), int(horizon))

    top_returns: list[float] = []
    benchmark_returns: list[float] = []
    gross_excess: list[float] = []
    net_excess: list[float] = []
    turnovers: list[float] = []
    costs: list[float] = []
    names: list[int] = []
    universe_sizes: list[int] = []
    previous: set[str] = set()
    for position in positions:
        row = factor.iloc[position]
        forward_row = forward.iloc[position]
        valid = pd.concat(
            [row.rename("f"), forward_row.rename("r")], axis=1
        ).dropna()
        if len(valid) < max(int(min_names), 10):
            continue
        n_top = max(int(min_names), int(round(len(valid) * float(top_fraction))))
        n_top = min(n_top, len(valid) - 1)
        top = valid["f"].nlargest(n_top).index
        gross = float(valid.loc[top, "r"].mean() - valid["r"].mean())
        current = set(str(item) for item in top)
        turnover = float(len(current - previous) / max(1, len(current)))
        previous = current
        cost = turnover * float(cost_bps) / 10000.0
        net = gross - cost
        top_returns.append(float(valid.loc[top, "r"].mean()) - cost)
        benchmark_returns.append(float(valid["r"].mean()))
        gross_excess.append(gross)
        net_excess.append(net)
        turnovers.append(turnover)
        costs.append(cost)
        names.append(len(valid))
        if universe_mask is not None:
            universe_sizes.append(int(universe_mask.iloc[position].sum()))

    if not net_excess:
        nan = float("nan")
        return {
            "entry_rule": entry,
            "horizon": int(horizon),
            "cost_bps": float(cost_bps),
            "n_dates": 0,
            "avg_names": 0.0,
            "long_only_gross_excess_per_period": nan,
            "long_only_net_excess_per_period": nan,
            "long_only_net_excess_annualized": nan,
            "long_only_te_annualized": nan,
            "long_only_t": nan,
            "turnover": nan,
            "cost_per_period": nan,
            "positive_ratio": nan,
            "universe_size_avg": (
                None if not universe_sizes else float(np.mean(universe_sizes))
            ),
        }

    periods_per_year = TRADING_DAYS / max(1, int(horizon))
    stats = _ls_stats(
        np.asarray(top_returns, dtype="float64"),
        np.asarray(benchmark_returns, dtype="float64"),
        periods_per_year=periods_per_year,
    )
    return {
        "entry_rule": entry,
        "horizon": int(horizon),
        "cost_bps": float(cost_bps),
        "n_dates": int(len(net_excess)),
        "avg_names": float(np.mean(names)),
        "long_only_gross_excess_per_period": float(np.mean(gross_excess)),
        "long_only_net_excess_per_period": float(np.mean(net_excess)),
        "long_only_net_excess_annualized": float(stats.get("excess", np.nan)),
        "long_only_te_annualized": float(stats.get("te", np.nan)),
        "long_only_t": float(stats.get("t", np.nan)),
        "turnover": float(np.mean(turnovers)),
        "cost_per_period": float(np.mean(costs)),
        "positive_ratio": float(stats.get("win", np.nan)),
        "universe_size_avg": (
            None if not universe_sizes else float(np.mean(universe_sizes))
        ),
    }
