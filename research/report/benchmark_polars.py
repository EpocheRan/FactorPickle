"""Real-Panel pandas vs Polars parity/benchmark for report hot paths.

Run:

    python -m research.report.benchmark_polars --universe pool:2022-01-04:200

This does not change the production metrics backend.  It loads one real Panel,
computes one factor, then compares rank IC and a simple forward-return quantile
spread between the pandas and Polars implementations.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from . import metrics
from .panel import price_factor_panel
from .panel_adapter import load_panel
from .metrics_polars import quantile_forward_spread, quantile_nav, rank_ic_frame


def _pandas_quantile_forward_spread(
    factor: pd.DataFrame,
    forward: pd.DataFrame,
    *,
    groups: int = 5,
    min_names: int | None = None,
) -> pd.Series:
    common = factor.columns.intersection(forward.columns)
    left = factor[common].stack(dropna=True).rename("f")
    right = forward[common].stack(dropna=True).rename("r")
    joined = pd.concat([left, right], axis=1).dropna()
    minimum = int(min_names if min_names is not None else groups * 5)
    rows: dict[pd.Timestamp, float] = {}
    for timestamp, group in joined.groupby(level=0):
        if len(group) < minimum:
            continue
        ranked = group["f"].rank(method="first")
        buckets = pd.qcut(ranked, groups, labels=False, duplicates="drop")
        means = group.groupby(buckets)["r"].mean()
        if 0 in means.index and groups - 1 in means.index:
            rows[pd.Timestamp(timestamp)] = float(means.loc[groups - 1] - means.loc[0])
    return pd.Series(rows).sort_index()


def _time(fn, repeat: int = 3) -> tuple[Any, float]:
    timings = []
    value = None
    for _ in range(repeat):
        started = time.perf_counter()
        value = fn()
        timings.append(time.perf_counter() - started)
    return value, float(np.median(timings))


def benchmark(
    *,
    universe: str = "pool:2022-01-04:200",
    factor_id: str = "momentum.ret_21d",
    start: str = "2022-01-04",
    end: str = "2026-11-01",
    horizon: int = 5,
    groups: int = 5,
    repeat: int = 3,
) -> dict[str, Any]:
    started = time.perf_counter()
    panel = load_panel(None, None, start, end, universe=universe, use_cache=True)
    panel_load_sec = time.perf_counter() - started
    factor = price_factor_panel(factor_id, panel)
    forward = metrics.forward_returns(panel.close, panel.open, (horizon,))[horizon]
    prepare_sec = time.perf_counter() - started
    load_sec = prepare_sec

    pandas_ic, pandas_ic_sec = _time(lambda: metrics._rank_ic_frame(factor, forward), repeat)
    polars_ic, polars_ic_sec = _time(lambda: rank_ic_frame(factor, forward), repeat)
    ic_common = pandas_ic.index.intersection(polars_ic.index)
    ic_diff = float(
        np.nanmax(np.abs(pandas_ic.reindex(ic_common).to_numpy() - polars_ic.reindex(ic_common).to_numpy()))
    ) if len(ic_common) else None

    pandas_spread, pandas_spread_sec = _time(
        lambda: _pandas_quantile_forward_spread(factor, forward, groups=groups), repeat
    )
    polars_spread, polars_spread_sec = _time(
        lambda: quantile_forward_spread(factor, forward, groups=groups), repeat
    )
    spread_common = pandas_spread.index.intersection(polars_spread.index)
    spread_diff = float(
        np.nanmax(
            np.abs(
                pandas_spread.reindex(spread_common).to_numpy()
                - polars_spread.reindex(spread_common).to_numpy()
            )
        )
    ) if len(spread_common) else None

    pandas_q, pandas_q_sec = _time(
        lambda: metrics._quantile_nav(
            factor,
            panel.close.reindex(factor.index),
            panel.open.reindex(factor.index),
            groups=groups,
            horizon=horizon,
        ),
        repeat,
    )
    polars_q, polars_q_sec = _time(
        lambda: quantile_nav(
            factor,
            panel.close.reindex(factor.index),
            panel.open.reindex(factor.index),
            groups=groups,
            horizon=horizon,
        ),
        repeat,
    )

    def _max_diff(left: pd.DataFrame, right: pd.DataFrame) -> float | None:
        common_index = left.index.intersection(right.index)
        common_columns = left.columns.intersection(right.columns)
        if len(common_index) == 0 or len(common_columns) == 0:
            return None
        delta = (
            left.loc[common_index, common_columns].to_numpy(dtype="float64")
            - right.loc[common_index, common_columns].to_numpy(dtype="float64")
        )
        return float(np.nanmax(np.abs(delta))) if np.isfinite(delta).any() else None

    quantile_parity = {
        "periods_gross": _max_diff(pandas_q.periods_gross, polars_q.periods_gross),
        "periods_net": _max_diff(pandas_q.periods_net, polars_q.periods_net),
        "turnover_df": _max_diff(pandas_q.turnover_df, polars_q.turnover_df),
        "nav": _max_diff(pandas_q.nav, polars_q.nav),
    }

    return {
        "schema_version": "factor_lab.polars_benchmark.v1",
        "universe": universe,
        "factor_id": factor_id,
        "start": start,
        "end": end,
        "horizon": horizon,
        "shape": {"rows": int(factor.shape[0]), "symbols": int(factor.shape[1])},
        "load_sec": round(load_sec, 3),
        "panel_load_sec": round(panel_load_sec, 3),
        "prepare_sec": round(prepare_sec, 3),
        "rank_ic": {
            "pandas_sec": round(pandas_ic_sec, 4),
            "polars_sec": round(polars_ic_sec, 4),
            "speedup": round(pandas_ic_sec / polars_ic_sec, 3) if polars_ic_sec else None,
            "pandas_n": int(len(pandas_ic)),
            "polars_n": int(len(polars_ic)),
            "max_abs_diff": ic_diff,
        },
        "quantile_spread": {
            "pandas_sec": round(pandas_spread_sec, 4),
            "polars_sec": round(polars_spread_sec, 4),
            "speedup": round(pandas_spread_sec / polars_spread_sec, 3) if polars_spread_sec else None,
            "pandas_n": int(len(pandas_spread)),
            "polars_n": int(len(polars_spread)),
            "max_abs_diff": spread_diff,
        },
        "quantile_nav": {
            "pandas_sec": round(pandas_q_sec, 4),
            "polars_sec": round(polars_q_sec, 4),
            "speedup": round(pandas_q_sec / polars_q_sec, 3) if polars_q_sec else None,
            "max_abs_diff": quantile_parity,
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--universe", default="pool:2022-01-04:200")
    parser.add_argument("--factor-id", default="momentum.ret_21d")
    parser.add_argument("--start", default="2022-01-04")
    parser.add_argument("--end", default="2026-11-01")
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--out", default="output/factor_lab/polars_benchmark.json")
    args = parser.parse_args(argv)
    result = benchmark(
        universe=args.universe,
        factor_id=args.factor_id,
        start=args.start,
        end=args.end,
        repeat=args.repeat,
    )
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
