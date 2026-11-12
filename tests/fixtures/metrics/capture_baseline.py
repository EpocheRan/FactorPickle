"""Capture the frozen research / Alpha metric characterization baselines.

This script is not a test.  It is the characterization harness used once to
freeze the behaviour of ``research.report.metrics.build_report`` (the calibrated
source of truth) and of the Alpha evaluator.  The generated JSON fixtures are
committed as regression baselines and only change with an explicit semantic
change.

Run:
    python tests/fixtures/metrics/capture_baseline.py \
        --research-out tests/fixtures/metrics/research_baseline.json \
        --alpha-out output/deliverables/a_full_alpha_before.json
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd


def build_panel(seed: int = 42, days: int = 320, symbols: int = 60):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2019-01-02", periods=days)
    columns = [f"{600000 + i:06d}" for i in range(symbols)]
    rets = pd.DataFrame(rng.normal(0.0004, 0.017, (days, symbols)), index=dates, columns=columns)
    close = 10.0 * (1.0 + rets).cumprod()
    noise = pd.DataFrame(rng.normal(0.0, 0.004, (days, symbols)), index=dates, columns=columns)
    open_ = close * (1.0 + noise)
    high = np.maximum(close, open_) * (1.0 + rng.uniform(0, 0.02, (days, symbols)))
    low = np.minimum(close, open_) * (1.0 - rng.uniform(0, 0.02, (days, symbols)))
    volume = pd.DataFrame(rng.lognormal(12, 0.5, (days, symbols)), index=dates, columns=columns)
    amount = volume * close * 10.0
    market_cap = pd.DataFrame(
        rng.lognormal(23, 0.8, (days, symbols)), index=dates, columns=columns
    )
    industry_names = [f"IND{i}" for i in range(8)]
    industry = pd.Series(
        [industry_names[i % len(industry_names)] for i in range(symbols)], index=columns
    )
    regime = pd.Series(
        np.where(rng.random(days) < 0.4, "bull", np.where(rng.random(days) < 0.5, "bear", "neutral")),
        index=dates,
    )
    is_st = {code: bool(index % 7 == 0) for index, code in enumerate(columns)}
    return {
        "dates": dates,
        "columns": columns,
        "close": close,
        "open": open_,
        "high": high,
        "low": low,
        "volume": volume,
        "amount": amount,
        "market_cap": market_cap,
        "industry": industry,
        "regime": regime,
        "is_st": is_st,
    }


def build_factors(panel) -> dict[str, pd.DataFrame]:
    close = panel["close"]
    returns = close.pct_change(fill_method=None)
    columns = panel["columns"]
    mom20 = close / close.shift(20) - 1.0
    vol20 = -returns.rolling(20, min_periods=10).std()
    rng = np.random.default_rng(7)
    # a stationary random factor: one fixed cross-sectional draw per date
    random_factor = pd.DataFrame(
        rng.normal(0, 1, (len(panel["dates"]), len(columns))),
        index=panel["dates"],
        columns=columns,
    )
    return {
        "mom20": mom20.loc[panel["dates"][40:]],
        "vol20": vol20.loc[panel["dates"][40:]],
        "random": random_factor.loc[panel["dates"][40:]],
    }


def jsonable(value):
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        number = float(value)
        return number if math.isfinite(number) else None
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, (pd.Timestamp,)):
        return value.isoformat()
    if isinstance(value, pd.DataFrame):
        return {
            "type": "dataframe",
            "columns": [str(c) for c in value.columns],
            "index": [str(i) for i in value.index],
            "data": [[jsonable(item) for item in row] for row in value.to_numpy().tolist()],
        }
    if isinstance(value, pd.Series):
        return {
            "type": "series",
            "name": str(value.name) if value.name is not None else None,
            "index": [str(i) for i in value.index],
            "data": [jsonable(item) for item in value.tolist()],
        }
    if isinstance(value, dict):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(item) for item in value]
    return str(value)


def capture_research(panel, factors) -> dict:
    from research.report import metrics

    out = {}
    for label, factor in factors.items():
        report = metrics.build_report(
            factor,
            panel["close"],
            panel["open"],
            factor_id=label,
            category="test",
            direction=1,
            universe="test",
            horizons=(1, 5, 20),
            primary_horizon=5,
            groups=5,
            cost_bps=20.0,
            amount=panel["amount"],
            low=panel["low"],
            definition={"source": "capture_baseline"},
            pool={"selection": "synthetic"},
            full=True,
            sections=("definition", "timing", "cross_section", "rating"),
        )
        key = label
        out[key] = {
            "n_symbols": report.n_symbols,
            "n_dates": report.n_dates,
            "coverage": report.coverage,
            "start": str(report.start),
            "end": str(report.end),
            "ic_stats": jsonable(report.ic_stats),
            "quantile_returns": jsonable(report.quantile_returns),
            "quantile_returns_gross": jsonable(report.quantile_returns_gross),
            "spread_gross": jsonable(report.spread_gross),
            "spread_net": jsonable(report.spread_net),
            "cost_mean": jsonable(report.cost_mean),
            "turnover": jsonable(report.turnover),
            "turnover_by_group": jsonable(report.turnover_by_group),
            "winsor": jsonable(report.winsor),
            "return_winsor": jsonable(report.return_winsor),
            "filter_test": jsonable(report.filter_test),
            "filter_test_opposite": jsonable(report.filter_test_opposite),
            "tradeability": jsonable(report.tradeability),
            "cost_table": jsonable(report.cost_table),
            "capacity": jsonable(report.capacity),
            "long_short": jsonable(report.long_short),
            "perf": jsonable(report.perf),
            "annual_returns": jsonable(report.annual_returns),
            "decay": jsonable(report.decay),
            "decay_note": report.decay_note,
            "monotonicity": jsonable(report.monotonicity),
            "monotonicity_net": jsonable(report.monotonicity_net),
            "monotonicity_p": jsonable(report.monotonicity_p),
            "tail_specificity": jsonable(report.tail_specificity),
            "sigma_5d": jsonable(report.sigma_5d),
            "cross_section_std": jsonable(report.cross_section_std),
            "limit_up": jsonable(report.limit_up),
            "ic_half": jsonable(report.ic_half),
            "ic_by_year": jsonable(report.ic_by_year),
            "ic_by_month": jsonable(report.ic_by_month),
            "ic_neutral": jsonable(report.ic_neutral),
            "limit_up": jsonable(report.limit_up),
            "diagnostics": jsonable([item.as_dict() for item in report.diagnostics]),
            "warnings": list(report.warnings),
            "quantile_ci": jsonable(report.quantile_ci),
        }
        # 5 日 IC 序列（逐日）单独存，便于逐点比对
        out[key]["ic_5"] = jsonable(report.ic.get(5))
        out[key]["ic_1"] = jsonable(report.ic.get(1))
        out[key]["ic_20"] = jsonable(report.ic.get(20))
    return out


def capture_research_complex(panel, factors) -> dict:
    """Call build_report with every optional input to cover the extra branches."""
    from research.report import metrics

    factor = factors["mom20"]
    peers = {name: value for name, value in factors.items() if name != "mom20"}
    report = metrics.build_report(
        factor,
        panel["close"],
        panel["open"],
        factor_id="mom20_complex",
        category="test",
        direction=1,
        universe="test",
        horizons=(1, 5, 20),
        primary_horizon=5,
        groups=5,
        cost_bps=20.0,
        regime=panel["regime"],
        regime_alt=panel["regime"],
        market_cap=panel["market_cap"],
        industry=panel["industry"],
        amount=panel["amount"],
        low=panel["low"],
        benchmark=panel["close"].mean(axis=1),
        peers=peers,
        neutralize=True,
        is_st=panel["is_st"],
        exclude_limit_up=True,
        full=True,
        sections=("definition", "timing", "cross_section", "rating"),
    )
    return {
        "ic_stats": jsonable(report.ic_stats),
        "spread_gross": jsonable(report.spread_gross),
        "spread_net": jsonable(report.spread_net),
        "quantile_returns": jsonable(report.quantile_returns),
        "quantile_returns_gross": jsonable(report.quantile_returns_gross),
        "winsor": jsonable(report.winsor),
        "return_winsor": jsonable(report.return_winsor),
        "ic_neutral": jsonable(report.ic_neutral),
        "strat_ic": jsonable(report.strat_ic),
        "tradeability": jsonable(report.tradeability),
        "sell_side": jsonable(report.sell_side),
        "cost_table": jsonable(report.cost_table),
        "capacity": jsonable(report.capacity),
        "incrementality": jsonable({
            k: v for k, v in (report.incrementality or {}).items() if k != "residual"
        }),
        "peers": jsonable(report.peers),
        "regime_stats": jsonable(report.regime_stats),
        "ic_by_size": jsonable(report.ic_by_size),
        "ic_by_industry": jsonable(report.ic_by_industry),
        "long_short": jsonable(report.long_short),
        "limit_up": jsonable(report.limit_up),
        "tradeability": jsonable(report.tradeability),
        "monotonicity": jsonable(report.monotonicity),
        "diagnostics": jsonable([item.as_dict() for item in report.diagnostics]),
        "warnings": list(report.warnings),
        "horizon_spreads": jsonable(report.horizon_spreads),
    }


def capture_alpha(panel, factors) -> dict:
    from factor_lab.alpha.compiler import AlphaContext
    from factor_lab.alpha.evaluation import evaluate_expression

    context = AlphaContext.from_fields(
        {
            "close": panel["close"],
            "open": panel["open"],
            "high": panel["high"],
            "low": panel["low"],
            "volume": panel["volume"],
            "amount": panel["amount"],
            "returns": panel["close"].pct_change(fill_method=None),
        },
        close=panel["close"],
    )
    expressions = [
        "rank(close)",
        "neg(ts_max(returns, 60))",
        "rank(ts_corr(rank(close), rank(volume), 20))",
    ]
    out = {}
    for expression in expressions:
        result = evaluate_expression(
            expression, context, horizon=5, groups=5, cost_bps=20.0
        )
        out[expression] = jsonable(result)
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--research-out", default="tests/fixtures/metrics/research_baseline.json")
    parser.add_argument("--alpha-out", default="output/deliverables/a_full_alpha_before.json")
    args = parser.parse_args()

    panel = build_panel()
    factors = build_factors(panel)
    research = {
        "schema": "factor_lab.metrics_baseline.v1",
        "captured_at": "pre-A-full",
        "reports": capture_research(panel, factors),
        "complex_report": capture_research_complex(panel, factors),
    }
    alpha = {
        "schema": "factor_lab.alpha_metrics_baseline.v1",
        "captured_at": "pre-A-full",
        "expressions": capture_alpha(panel, factors),
    }
    research_path = Path(args.research_out)
    research_path.parent.mkdir(parents=True, exist_ok=True)
    research_path.write_text(
        json.dumps(research, ensure_ascii=False, indent=1, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    alpha_path = Path(args.alpha_out)
    alpha_path.parent.mkdir(parents=True, exist_ok=True)
    alpha_path.write_text(
        json.dumps(alpha, ensure_ascii=False, indent=1, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {research_path} ({research_path.stat().st_size} bytes)")
    print(f"wrote {alpha_path} ({alpha_path.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
