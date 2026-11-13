"""Human-readable benchmark corpus for Phase 0.

This is a benchmark, not an idea catalog: the search engine does not use it to
generate its own candidates.  Expressions are deliberately small, auditable
and drawn from the public 101 Formulaic Alphas / Alpha158 families.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Baseline:
    name: str
    expression: str
    family: str
    source: str


HUMAN_BASELINES: tuple[Baseline, ...] = (
    Baseline("a001", "rank(ts_delta(log(close), 1))", "101-alpha", "101 Formulaic Alphas"),
    Baseline("a002", "-1 * ts_corr(rank(close), rank(volume), 10)", "101-alpha", "101 Formulaic Alphas"),
    Baseline("a003", "rank(ts_corr(rank(close), rank(volume), 10))", "101-alpha", "101 Formulaic Alphas"),
    Baseline("a004", "-1 * rank(ts_rank(close, 10))", "101-alpha", "101 Formulaic Alphas"),
    Baseline("a005", "rank(ts_delta(close, 5))", "101-alpha", "101 Formulaic Alphas"),
    Baseline("a006", "-1 * ts_delta(close, 5)", "101-alpha", "101 Formulaic Alphas"),
    Baseline("a007", "rank(ts_corr(rank(close), rank(amount), 20))", "101-alpha", "101 Formulaic Alphas"),
    Baseline("a008", "-1 * rank(ts_std(returns, 20))", "101-alpha", "101 Formulaic Alphas"),
    Baseline("a009", "rank(ts_mean(amount, 20))", "101-alpha", "101 Formulaic Alphas"),
    Baseline("a010", "-1 * ts_rank(close, 20)", "101-alpha", "101 Formulaic Alphas"),
    Baseline("a011", "rank(ts_delta(close, 20))", "101-alpha", "101 Formulaic Alphas"),
    Baseline("a012", "-1 * ts_delta(ts_mean(close, 5), 5)", "101-alpha", "101 Formulaic Alphas"),
    Baseline("a013", "rank(div(close, ts_mean(close, 20)))", "101-alpha", "101 Formulaic Alphas"),
    Baseline("a014", "-1 * rank(div(amount, ts_mean(amount, 20)))", "101-alpha", "101 Formulaic Alphas"),
    Baseline("a015", "rank(ts_corr(close, volume, 20))", "101-alpha", "101 Formulaic Alphas"),
    Baseline("a016", "-1 * ts_corr(close, open, 10)", "101-alpha", "101 Formulaic Alphas"),
    Baseline("a017", "rank(div(high, low))", "101-alpha", "101 Formulaic Alphas"),
    Baseline("a018", "-1 * rank(div(close, ts_max(high, 20)))", "101-alpha", "101 Formulaic Alphas"),
    Baseline("a019", "rank(div(close, ts_min(low, 20)))", "101-alpha", "101 Formulaic Alphas"),
    Baseline("a020", "-1 * rank(ts_corr(rank(close), rank(high), 5))", "101-alpha", "101 Formulaic Alphas"),
    Baseline("kbar01", "rank(div(sub(close, open), open))", "alpha158", "Alpha158 K-bar"),
    Baseline("kbar02", "rank(div(sub(high, low), open))", "alpha158", "Alpha158 K-bar"),
    Baseline("kbar03", "rank(div(sub(high, close), open))", "alpha158", "Alpha158 K-bar"),
    Baseline("kbar04", "rank(div(sub(close, low), open))", "alpha158", "Alpha158 K-bar"),
    Baseline("v01", "rank(div(close, ts_mean(close, 5)))", "alpha158", "Alpha158 price"),
    Baseline("v02", "rank(div(close, ts_mean(close, 20)))", "alpha158", "Alpha158 price"),
    Baseline("v03", "rank(ts_std(returns, 20))", "alpha158", "Alpha158 volatility"),
    Baseline("v04", "rank(ts_corr(close, volume, 10))", "alpha158", "Alpha158 volume"),
    Baseline("v05", "rank(div(amount, ts_mean(amount, 20)))", "alpha158", "Alpha158 volume"),
    Baseline("v06", "rank(ts_beta(returns, ts_mean(returns, 5), 20))", "alpha158", "Alpha158 regression"),
)


def baseline_by_name() -> dict[str, Baseline]:
    return {item.name: item for item in HUMAN_BASELINES}


def baseline_expressions() -> list[str]:
    return [item.expression for item in HUMAN_BASELINES]
