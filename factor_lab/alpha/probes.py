"""Instrument-calibration probes (P0): cheat / random / known.

These probes do **not** produce alpha candidates.  They answer one question:
is the research instrument (data -> evaluation -> gate) trustworthy before we
interpret hundreds of trial results?

- cheat: a factor built from its own forward return must pass the mechanical
  checks; a failure means the data/timing/cost/gate pipeline is broken.
- random: random factors must pass the nominal one-sided 5% test at about 5%;
  far above means the gate/trial accounting is too lax, far below means it is
  broken or over-conservative.
- known: a small set of established factors should show at least a few
  significant results; all-fail points at dead data/factor family, not at the
  search strategy.

Probes are calibration, not candidate trials: they are recorded in a separate
``probes/`` report and never enter the family FDR denominator.
"""

from __future__ import annotations

import math
import uuid
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

from factor_lab.alpha.baselines import HUMAN_BASELINES
from factor_lab.alpha.data import AlphaDataset
from factor_lab.alpha.evaluation import evaluate_expression, evaluate_factor
from factor_lab.core import config
from factor_lab.core.store import write_json_atomic
from factor_lab.alpha.trials import p_value_from_t


PROBE_SCHEMA = "factor_lab.alpha.probes.v1"
PROBE_DIR = "probes"
RANDOM_ALPHA = 0.05
KNOWN_EXPECTED_MIN = 3

#: A compact, pre-registered known-factor set across mechanisms.  These are
#: calibration references, not candidates.
KNOWN_EXPRESSIONS: tuple[tuple[str, str], ...] = (
    ("momentum", "rank(ts_delta(close, 20))"),
    ("reversal", "neg(rank(ts_delta(close, 5)))"),
    ("low_volatility", "neg(rank(ts_std(returns, 20)))"),
    ("value_pb", "neg(rank(pb_mrq))"),
    ("size", "neg(rank(ts_mean(float_mv, 5)))"),
    ("money_flow", "rank(ts_mean(mf_net_amount, 5))"),
)


def probe_dir() -> Path:
    path = config.LAB_ROOT / "alpha" / PROBE_DIR
    path.mkdir(parents=True, exist_ok=True)
    return path


def _finite(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _one_sided_p(net_t: Any, n_dates: Any) -> float:
    if not _finite(net_t) or not _finite(n_dates) or float(net_t) <= 0:
        return 1.0
    return 0.5 * p_value_from_t(float(net_t), int(n_dates))


def _signal_pass(metrics: dict[str, Any], *, min_dates: int = 20) -> bool:
    """Two-sided 5% signal calibration used by random/known probes.

    Random/known probes must test the *signal* statistic (IC t / gross t), not
    the post-cost deployability gate: random factors turn over ~80% per
    rebalance and the cost floor would otherwise make the calibration test
    impossible.  Mechanical cost/turnover checks are covered by the cheat probe.
    """
    if not _finite(metrics.get("ic_t")):
        return False
    if int(metrics.get("n_dates") or 0) < int(min_dates):
        return False
    return p_value_from_t(float(metrics["ic_t"]), int(metrics["n_dates"])) < RANDOM_ALPHA


def _metrics_subset(metrics: dict[str, Any]) -> dict[str, Any]:
    return {
        key: metrics.get(key)
        for key in (
            "n_dates",
            "coverage",
            "ic_mean",
            "ic_t",
            "gross_spread",
            "gross_t",
            "net_spread",
            "net_t",
            "turnover",
            "cost_per_period",
            "capacity_multiple",
            "universe_size_avg",
        )
    }


def _binomial_ci(p: float, n: int) -> list[float]:
    if n <= 0:
        return [0.0, 0.0]
    se = math.sqrt(max(p * (1.0 - p), 0.0) / n)
    return [max(0.0, p - 1.96 * se), min(1.0, p + 1.96 * se)]


def _run_cheat(
    dataset: AlphaDataset,
    *,
    horizon: int,
    groups: int,
    cost_bps: float,
    universe: pd.DataFrame | None,
) -> dict[str, Any]:
    from factor_metrics import forward_returns

    close = dataset.context.close
    open_ = dataset.context.fields.get("open")
    if open_ is None:
        raise ValueError("cheat probe requires an open field")
    start, end = dataset.start, dataset.end
    forward = forward_returns(close, open_, (horizon,))[horizon]
    factor = forward.loc[(forward.index >= pd.Timestamp(start)) & (forward.index <= pd.Timestamp(end))]
    metrics = evaluate_factor(
        factor,
        close,
        open_=open_,
        horizon=horizon,
        groups=groups,
        cost_bps=cost_bps,
        universe=universe,
    )
    checks = {
        "coverage_gt_0_5": _finite(metrics.get("coverage")) and float(metrics["coverage"]) > 0.5,
        "n_dates_ge_20": int(metrics.get("n_dates") or 0) >= 20,
        "ic_mean_gt_0_5": _finite(metrics.get("ic_mean")) and float(metrics["ic_mean"]) > 0.5,
        "net_spread_positive": _finite(metrics.get("net_spread")) and float(metrics["net_spread"]) > 0.0,
        "net_t_gt_2": _finite(metrics.get("net_t")) and float(metrics["net_t"]) > 2.0,
    }
    return {
        "construction": "factor[t] = close[t+h]/open[t+1]-1 (future information; intentional cheat)",
        "metrics": _metrics_subset(metrics),
        "checks": checks,
        "passed": all(checks.values()),
    }


def _run_random(
    dataset: AlphaDataset,
    *,
    horizon: int,
    groups: int,
    cost_bps: float,
    universe: pd.DataFrame | None,
    n: int,
    seed: int,
) -> dict[str, Any]:
    close = dataset.context.close
    index = close.index[(close.index >= pd.Timestamp(dataset.start)) & (close.index <= pd.Timestamp(dataset.end))]
    columns = close.columns
    rng = np.random.default_rng(int(seed))
    passed = 0
    positive_net = 0
    net_t_above_2 = 0
    details: list[dict[str, Any]] = []
    for i in range(int(n)):
        factor = pd.DataFrame(
            rng.normal(size=(len(index), len(columns))), index=index, columns=columns
        )
        metrics = evaluate_factor(
            factor,
            close,
            open_=dataset.context.fields.get("open"),
            horizon=horizon,
            groups=groups,
            cost_bps=cost_bps,
            universe=universe,
        )
        ok = _signal_pass(metrics)
        passed += int(ok)
        positive_net += int(_finite(metrics.get("net_spread")) and float(metrics["net_spread"]) > 0)
        net_t_above_2 += int(_finite(metrics.get("net_t")) and float(metrics["net_t"]) >= 2.0)
        if len(details) < 5:
            details.append({"sample": i, "passed": ok, "metrics": _metrics_subset(metrics)})
    rate = passed / max(1, int(n))
    return {
        "n": int(n),
        "seed": int(seed),
        "expected_rate": RANDOM_ALPHA,
        "observed_rate": rate,
        "expected_pass": int(round(RANDOM_ALPHA * int(n))),
        "definition": "|IC t| two-sided p < 0.05 (signal calibration; post-cost net is reported only for audit)",
        "n_passed": passed,
        "n_positive_net": positive_net,
        "net_t_above_2_rate": net_t_above_2 / max(1, int(n)),
        "binomial_ci95": _binomial_ci(rate, int(n)),
        "details": details,
    }


def _run_known(
    dataset: AlphaDataset,
    *,
    horizon: int,
    groups: int,
    cost_bps: float,
    universe: pd.DataFrame | None,
    known: Iterable[tuple[str, str]] | None = None,
) -> dict[str, Any]:
    rows = []
    for name, expression in list(known or KNOWN_EXPRESSIONS):
        try:
            metrics = evaluate_expression(
                expression,
                dataset.context,
                horizon=horizon,
                groups=groups,
                cost_bps=cost_bps,
                start=dataset.start,
                end=dataset.end,
                universe=universe,
            )
            rows.append(
                {
                    "name": name,
                    "expression": expression,
                    "passed": _signal_pass(metrics),
                    "metrics": _metrics_subset(metrics),
                }
            )
        except Exception as exc:  # noqa: BLE001
            rows.append(
                {
                    "name": name,
                    "expression": expression,
                    "passed": False,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
    passed = sum(1 for row in rows if row["passed"])
    net_t_positive = sum(1 for row in rows if _finite((row.get("metrics") or {}).get("net_t")) and float((row.get("metrics") or {})["net_t"]) > 0)
    return {
        "n": len(rows),
        "n_net_t_positive": net_t_positive,
        "expected_min": min(KNOWN_EXPECTED_MIN, len(rows)),
        "n_passed": passed,
        "definition": "|IC t| two-sided p < 0.05; direction is reported per factor but not used for calibration",
        "passed": passed >= min(KNOWN_EXPECTED_MIN, len(rows)),
        "factors": rows,
    }


def run_probes(
    dataset: AlphaDataset,
    *,
    horizon: int = 5,
    groups: int = 5,
    cost_bps: float = 20.0,
    universe: pd.DataFrame | None = None,
    universe_summary: dict[str, Any] | None = None,
    random_n: int = 200,
    seed: int = 20261112,
    known: Iterable[tuple[str, str]] | None = None,
    out_path: str | Path | None = None,
) -> dict[str, Any]:
    """Run the three calibration probes and write one auditable report."""
    probe_id = f"PRB-{uuid.uuid4().hex[:8]}"
    cheat = _run_cheat(
        dataset, horizon=horizon, groups=groups, cost_bps=cost_bps, universe=universe
    )
    random = _run_random(
        dataset,
        horizon=horizon,
        groups=groups,
        cost_bps=cost_bps,
        universe=universe,
        n=random_n,
        seed=seed,
    )
    known_result = _run_known(
        dataset,
        horizon=horizon,
        groups=groups,
        cost_bps=cost_bps,
        universe=universe,
        known=known,
    )

    random_ok = (
        random["observed_rate"] <= 0.15
        and random["observed_rate"] >= 0.01
    )
    if not cheat["passed"]:
        conclusion = "A_pipeline_bias"
        action = "stop_and_fix_pipeline"
        trusted = False
    elif not known_result["passed"]:
        conclusion = "B_factor_family_dead_or_data_gap"
        action = "new_data_axes"
        trusted = True
    elif not random_ok:
        conclusion = "C_gate_or_trial_accounting_miscalibrated"
        action = "recalibrate_fdr_and_gate"
        trusted = True
    else:
        conclusion = "instrument_plausible"
        action = "proceed_to_fdr"
        trusted = True

    report = {
        "schema": PROBE_SCHEMA,
        "probe_id": probe_id,
        "created_at": config.now_iso(),
        "window": {"start": dataset.start, "end": dataset.end, "warmup_days": dataset.warmup_days},
        "universe": universe_summary or {},
        "config": {
            "horizon": int(horizon),
            "groups": int(groups),
            "cost_bps": float(cost_bps),
            "signal_alpha": RANDOM_ALPHA,
            "signal_test": "two-sided |IC t| p < 0.05 for random/known; mechanical cost/gate checks for cheat",
            "random_n": int(random_n),
            "seed": int(seed),
        },
        "cheat": cheat,
        "random": random,
        "known": known_result,
        "conclusion": {
            "code": conclusion,
            "recommended_action": action,
            "instrument_trusted": trusted,
            "interpretation": {
                "A_pipeline_bias": "cheat failed -> data/timing/cost/gate pipeline likely broken; do not trust research results until fixed",
                "B_factor_family_dead_or_data_gap": "cheat passed but known factors all/mostly fail -> factor family or data/eval is dead; switch data axes before more search",
                "C_gate_or_trial_accounting_miscalibrated": "cheat/known ok but random pass rate is outside the nominal band -> gate threshold/trial accounting needs recalibration",
                "instrument_plausible": "cheat/random/known are consistent with a working instrument; proceed to FDR with explicit N_eff",
            }[conclusion],
        },
    }
    target = Path(out_path) if out_path else probe_dir() / f"{probe_id}.json"
    report["path"] = str(target)
    write_json_atomic(target, report)
    return report
