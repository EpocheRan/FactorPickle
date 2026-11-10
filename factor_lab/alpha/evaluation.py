"""Native evaluation for Alpha DSL candidates.

This evaluator is intentionally independent of the production report package:
it turns a factor matrix into the metrics the search engine and Red Team need.
It enforces non-overlapping rebalance dates, forward returns, costs and
turnover; the production report remains the final human-facing report.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from factor_lab.alpha.compiler import AlphaContext, compile_expression


def _t_stat(values: pd.Series) -> float:
    clean = values.replace([np.inf, -np.inf], np.nan).dropna()
    if len(clean) < 3 or float(clean.std(ddof=1)) == 0.0:
        return float("nan")
    return float(clean.mean() / clean.std(ddof=1) * np.sqrt(len(clean)))


def _rank_corr(a: pd.Series, b: pd.Series) -> float:
    valid = pd.concat([a, b], axis=1).dropna()
    if len(valid) < 5:
        return float("nan")
    with np.errstate(invalid="ignore", divide="ignore"):
        return float(valid.iloc[:, 0].rank().corr(valid.iloc[:, 1].rank()))


def _monotonicity(means: list[float]) -> float:
    if len(means) < 3 or not np.isfinite(means).all():
        return float("nan")
    return float(pd.Series(means).rank().corr(pd.Series(range(len(means))), method="spearman"))


@dataclass
class AlphaMetrics:
    n_dates: int
    n_symbols: int
    coverage: float
    ic_mean: float
    ic_std: float
    icir: float
    ic_t: float
    ic_win: float
    gross_spread: float
    gross_t: float
    net_spread: float
    net_t: float
    turnover: float
    cost_per_period: float
    sharpe: float
    monotonicity: float
    group_means: list[float]
    capacity_multiple: float | None
    horizon: int
    rebalance: int
    groups: int
    cost_bps: float
    entry_rule: str = "next_open"
    capacity_target_aum: float = 50_000_000.0
    capacity_participation: float = 0.10
    winsorized_net_spread: float = float("nan")
    trimmed_net_spread: float = float("nan")

    def to_dict(self) -> dict[str, Any]:
        return {
            "n_dates": self.n_dates,
            "n_symbols": self.n_symbols,
            "coverage": round(float(self.coverage), 6),
            "ic_mean": round(float(self.ic_mean), 6),
            "ic_std": round(float(self.ic_std), 6),
            "icir": round(float(self.icir), 6),
            "ic_t": round(float(self.ic_t), 4),
            "ic_win": round(float(self.ic_win), 4),
            "gross_spread": round(float(self.gross_spread), 6),
            "gross_t": round(float(self.gross_t), 4),
            "net_spread": round(float(self.net_spread), 6),
            "net_t": round(float(self.net_t), 4),
            "turnover": round(float(self.turnover), 6),
            "cost_per_period": round(float(self.cost_per_period), 6),
            "sharpe": round(float(self.sharpe), 4),
            "monotonicity": round(float(self.monotonicity), 4),
            "group_means": [round(float(x), 6) for x in self.group_means],
            "capacity_multiple": None if self.capacity_multiple is None else round(float(self.capacity_multiple), 4),
            "capacity_target_aum": float(self.capacity_target_aum),
            "capacity_participation": float(self.capacity_participation),
            "winsorized_net_spread": round(float(self.winsorized_net_spread), 6),
            "trimmed_net_spread": round(float(self.trimmed_net_spread), 6),
            "horizon": int(self.horizon),
            "rebalance": int(self.rebalance),
            "entry_rule": self.entry_rule,
            "groups": int(self.groups),
            "cost_bps": float(self.cost_bps),
        }


def evaluate_factor(
    factor: pd.DataFrame,
    close: pd.DataFrame,
    *,
    horizon: int = 5,
    groups: int = 5,
    cost_bps: float = 20.0,
    amount: pd.DataFrame | None = None,
    open_: pd.DataFrame | None = None,
    min_names: int | None = None,
) -> dict[str, Any]:
    """Evaluate a factor matrix with non-overlapping forward horizons.

    ``net_spread`` is long-short Q_top - Q_bottom minus round-trip costs.
    ``turnover`` is one-way turnover of the long-short book at each rebalance.
    """
    horizon = max(1, int(horizon))
    n_symbols = int(close.shape[1])
    groups = max(2, min(int(groups), max(2, n_symbols // 2)))
    min_names = int(min_names or max(groups + 1, min(10, n_symbols // 2)))

    factor = factor.reindex(index=close.index, columns=close.columns).astype(float)
    factor = factor.replace([np.inf, -np.inf], np.nan)
    # Enter at the next bar's open: a signal computed from close_t cannot be
    # traded at close_t in A-shares.
    if open_ is not None:
        entry = open_.reindex(index=close.index, columns=close.columns).shift(-1)
        forward = close.shift(-horizon) / entry - 1.0
        entry_rule = "next_open"
    else:
        forward = close.shift(-horizon) / close - 1.0
        entry_rule = "same_close"
    step = horizon
    dates = list(factor.index[::step])
    ic_values: list[float] = []
    group_series: list[list[float]] = []
    accepted_dates: list[pd.Timestamp] = []
    long_weights: dict[pd.Timestamp, pd.Series] = {}
    short_weights: dict[pd.Timestamp, pd.Series] = {}
    winsor_spreads: list[float] = []
    trimmed_spreads: list[float] = []

    for date in dates:
        if date not in forward.index:
            continue
        f = factor.loc[date]
        r = forward.loc[date]
        valid = pd.concat([f.rename("f"), r.rename("r")], axis=1).dropna()
        if len(valid) < min_names:
            continue
        ic = _rank_corr(valid["f"], valid["r"])
        if np.isfinite(ic):
            ic_values.append(ic)
        try:
            labels = pd.qcut(valid["f"].rank(method="first"), groups, labels=False, duplicates="drop")
        except ValueError:
            continue
        means = valid.groupby(labels)["r"].mean()
        if len(means) < groups:
            continue
        group_series.append([float(means.get(i, np.nan)) for i in range(groups)])
        # Cross-sectional tail diagnostics: clipped and trimmed return spreads.
        w_returns = valid["r"].clip(
            valid["r"].quantile(0.01),
            valid["r"].quantile(0.99),
        )
        w_means = w_returns.groupby(labels).mean()
        winsor_spreads.append(float(w_means.get(groups - 1, np.nan) - w_means.get(0, np.nan)))
        trimmed = valid[(valid["r"] >= valid["r"].quantile(0.01)) & (valid["r"] <= valid["r"].quantile(0.99))]
        if len(trimmed) >= min_names:
            t_means = trimmed.groupby(labels.loc[trimmed.index])["r"].mean()
            if len(t_means) >= groups:
                trimmed_spreads.append(float(t_means.get(groups - 1, np.nan) - t_means.get(0, np.nan)))
        accepted_dates.append(date)
        top_names = valid.index[labels == groups - 1]
        bottom_names = valid.index[labels == 0]
        long_weights[date] = pd.Series(1.0 / max(1, len(top_names)), index=top_names, dtype=float)
        short_weights[date] = pd.Series(-1.0 / max(1, len(bottom_names)), index=bottom_names, dtype=float)

    if not group_series:
        raise ValueError("factor has no evaluable cross-sections")

    group_matrix = pd.DataFrame(group_series, index=accepted_dates)
    group_means = group_matrix.mean(axis=0)
    gross = group_means.iloc[-1] - group_means.iloc[0]
    gross_series = group_matrix.iloc[:, -1] - group_matrix.iloc[:, 0]
    winsor_series = pd.Series(winsor_spreads, dtype=float).dropna()
    trimmed_series = pd.Series(trimmed_spreads, dtype=float).dropna()

    turnover = 0.0
    if len(long_weights) >= 2:
        ordered = sorted(long_weights)
        changes = []
        for left, right in zip(ordered, ordered[1:]):
            lw = long_weights[left].reindex(close.columns).fillna(0.0)
            rw = long_weights[right].reindex(close.columns).fillna(0.0)
            ls = short_weights[left].reindex(close.columns).fillna(0.0)
            rs = short_weights[right].reindex(close.columns).fillna(0.0)
            changes.append(float((rw - lw).abs().sum() + (rs - ls).abs().sum()))
        turnover = float(np.mean(changes) / 2.0) if changes else 0.0
    cost_per_period = turnover * 2.0 * float(cost_bps) / 10000.0
    net_series = gross_series - cost_per_period
    net = float(net_series.mean())
    winsor_net = float(winsor_series.mean() - cost_per_period) if len(winsor_series) else float("nan")
    trimmed_net = float(trimmed_series.mean() - cost_per_period) if len(trimmed_series) else float("nan")
    ic_series = pd.Series(ic_values, dtype=float)
    std = float(ic_series.std(ddof=1)) if len(ic_series) > 2 else float("nan")
    icir = float(ic_series.mean() / std) if np.isfinite(std) and std > 0 else float("nan")
    ic_t = _t_stat(ic_series)
    gross_t = _t_stat(gross_series)
    net_t = _t_stat(net_series)
    sharpe = float(net_series.mean() / net_series.std(ddof=1) * np.sqrt(252.0 / horizon)) if len(net_series) > 2 and float(net_series.std(ddof=1)) > 0 else float("nan")
    capacity = None
    if amount is not None:
        cap_values = []
        for date, weights in long_weights.items():
            if date in amount.index:
                names = [name for name in weights.index if name in amount.columns]
                if names:
                    cap_values.append(float(amount.loc[date, names].median()))
        if cap_values:
            # Deployable capacity at 10% participation over the holding horizon,
            # relative to a ¥50m target AUM.  This is an explicit, auditable
            # assumption; it is not a claim about realized market impact.
            capacity = float(
                0.10 * np.nanmedian(cap_values) * float(horizon) / 50_000_000.0
            )

    factor_valid = factor.notna()
    metrics = AlphaMetrics(
        n_dates=len(group_series),
        n_symbols=int(factor.shape[1]),
        coverage=float(factor_valid.sum().sum() / max(1, factor.size)),
        ic_mean=float(ic_series.mean()) if len(ic_series) else float("nan"),
        ic_std=std,
        icir=icir,
        ic_t=ic_t,
        ic_win=float((ic_series > 0).mean()) if len(ic_series) else float("nan"),
        gross_spread=float(gross),
        gross_t=gross_t,
        net_spread=net,
        net_t=net_t,
        turnover=turnover,
        cost_per_period=cost_per_period,
        sharpe=sharpe,
        monotonicity=_monotonicity([float(x) for x in group_means]),
        group_means=[float(x) for x in group_means],
        capacity_multiple=capacity,
        horizon=horizon,
        rebalance=horizon,
        groups=groups,
        cost_bps=float(cost_bps),
        entry_rule=entry_rule,
        winsorized_net_spread=winsor_net,
        trimmed_net_spread=trimmed_net,
    )
    return metrics.to_dict()


def evaluate_expression(
    expression: str,
    context: AlphaContext,
    *,
    horizon: int = 5,
    groups: int = 5,
    cost_bps: float = 20.0,
    start: Any | None = None,
    end: Any | None = None,
) -> dict[str, Any]:
    factor, report = compile_expression(expression, context)
    if start is not None or end is not None:
        factor = factor.loc[
            (factor.index >= pd.Timestamp(start) if start is not None else True)
            & (factor.index <= pd.Timestamp(end) if end is not None else True)
        ]
    amount = context.fields.get("amount")
    open_ = context.fields.get("open")
    result = evaluate_factor(
        factor,
        context.close.loc[factor.index],
        horizon=horizon,
        groups=groups,
        cost_bps=cost_bps,
        amount=amount.loc[factor.index] if amount is not None else None,
        open_=open_.loc[factor.index] if open_ is not None else None,
    )
    result["expression"] = str(expression)
    result["validation"] = report.to_dict()
    return result
