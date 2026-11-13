"""Portfolio-level long-only backtest: construction + execution + risk metrics.

This is the bridge between the signal Gate and a deployable portfolio.  It is
still a paper backtest: no broker, no live orders, and no external security
master unless supplied.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from factor_lab.alpha.execution import ExecutionPolicy, execute_target_weights, load_execution_policy
from factor_lab.alpha.portfolio import PortfolioPolicy, build_long_only_weights, load_portfolio_policy


PORTFOLIO_RESULT_SCHEMA = "factor_lab.alpha.portfolio_backtest.v1"


def _period_t(series: pd.Series) -> tuple[float, float]:
    clean = series.dropna()
    if len(clean) < 3 or clean.std(ddof=1) == 0:
        return float("nan"), float("nan")
    mean = float(clean.mean())
    std = float(clean.std(ddof=1))
    return mean, mean / std * np.sqrt(len(clean))


def run_portfolio_backtest(
    factor: pd.DataFrame,
    context: Any,
    eligible: pd.DataFrame,
    *,
    start: str,
    end: str,
    horizon: int = 20,
    industry: pd.Series | None = None,
    portfolio_policy: PortfolioPolicy | None = None,
    execution_policy: ExecutionPolicy | None = None,
    security_master: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    portfolio_policy = portfolio_policy or load_portfolio_policy()
    execution_policy = execution_policy or load_execution_policy()
    close = context.close
    open_ = context.fields.get("open")
    amount = context.fields.get("amount")
    raw_close = context.fields.get("close_raw")
    float_mv = context.fields.get("float_mv")
    if close is None or open_ is None or amount is None:
        raise ValueError("portfolio backtest requires close/open/amount")
    dates = close.index[(close.index >= pd.Timestamp(start)) & (close.index <= pd.Timestamp(end))]
    positions = list(range(0, max(0, len(dates) - horizon - 1), horizon))
    previous_weights = pd.Series(dtype=float)
    period_net: list[float] = []
    period_gross: list[float] = []
    turnovers: list[float] = []
    costs: list[float] = []
    blocked: list[dict[str, Any]] = []
    industry_weights: list[dict[str, Any]] = []
    for position in positions:
        signal_date = dates[position]
        exec_position = min(position + 1, len(dates) - 1)
        entry_date = dates[exec_position]
        exit_position = min(exec_position + horizon, len(dates) - 1)
        exit_date = dates[exit_position]
        if exit_position <= exec_position:
            continue
        row_eligible = eligible.loc[signal_date].reindex(factor.columns).fillna(False).astype(bool)
        row_factor = factor.loc[signal_date].reindex(factor.columns)
        try:
            target, construction = build_long_only_weights(
                row_factor,
                row_eligible,
                context=context,
                industry=industry,
                float_mv=float_mv,
                previous_weights=previous_weights if len(previous_weights) else None,
                policy=portfolio_policy,
                as_of=signal_date,
            )
        except ValueError:
            continue
        adv = amount.loc[:signal_date].tail(20).mean()
        execution = execute_target_weights(
            previous_weights,
            target,
            date=entry_date,
            context=context,
            adv=adv,
            policy=execution_policy,
            security_master=security_master,
        )
        executed = execution["executed_weights"]
        # Forward return from next open to close at the horizon (adjusted).
        entry_open = open_.loc[entry_date]
        exit_close = close.loc[exit_date]
        forward = exit_close / entry_open - 1.0
        forward = forward.replace([np.inf, -np.inf], np.nan)
        benchmark_forward = forward.reindex(row_eligible.index).where(row_eligible).dropna()
        portfolio_forward = forward.reindex(executed.index).fillna(0.0)
        gross = float((executed * portfolio_forward).sum())
        benchmark = float(benchmark_forward.mean()) if len(benchmark_forward) else float("nan")
        total_cost = float(execution["total_cost"])
        net = gross - total_cost
        period_gross.append(gross - benchmark if np.isfinite(benchmark) else float("nan"))
        period_net.append(net - benchmark if np.isfinite(benchmark) else float("nan"))
        turnovers.append(float(execution["turnover"]))
        costs.append(total_cost)
        blocked.extend(execution["blocked"])
        industry_weights.append(construction.get("industry_weights") or {})
        previous_weights = executed
    periods_per_year = 243.0 / max(1, int(horizon))
    net_series = pd.Series(period_net, dtype=float).dropna()
    gross_series = pd.Series(period_gross, dtype=float).dropna()
    mean_net, t_net = _period_t(net_series)
    mean_gross, t_gross = _period_t(gross_series)
    vol = float(net_series.std(ddof=1) * np.sqrt(periods_per_year)) if len(net_series) > 2 else float("nan")
    return {
        "schema": PORTFOLIO_RESULT_SCHEMA,
        "evaluation_start": start,
        "evaluation_end": end,
        "horizon": int(horizon),
        "n_periods": int(len(net_series)),
        "portfolio_policy": portfolio_policy.to_dict(),
        "execution_policy": execution_policy.to_dict(),
        "gross_excess_annualized": mean_gross * periods_per_year,
        "net_excess_annualized": mean_net * periods_per_year,
        "gross_t": t_gross,
        "net_t": t_net,
        "net_te_annualized": vol,
        "turnover_mean": float(np.mean(turnovers)) if turnovers else None,
        "cost_mean": float(np.mean(costs)) if costs else None,
        "cost_to_gross": (float(np.mean(costs)) / abs(mean_gross)) if costs and mean_gross else None,
        "n_blocked": len(blocked),
        "blocked_sample": blocked[:20],
        "security_master_available": security_master is not None,
        "execution_data_gaps": [] if security_master is not None else ["security_master_missing: ST/new-stock/delisting flags not modeled"],
        "industry_weights_last": industry_weights[-1] if industry_weights else None,
        "note": "Paper portfolio backtest; no live execution. ST/new-stock/delisting flags require security_master; missing flags are explicit data gaps.",
    }
