"""P1 orthogonality check: exposure correlation against a small library snapshot.

P0 used a manual rho placeholder.  This module is the first real implementation:
it computes weekly cross-sectional Spearman correlations between the candidate
and a curated set of price factors, then block-bootstraps the mean correlation.

``rho_return`` is intentionally left pending: the price-only panel does not carry
a strategy PnL ledger.  The decision uses the exposure interval and says so.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from research.report.panel import price_factor_panel
from research.report.panel_adapter import load_panel

from . import config
from .schemas import OutcomeSpec
from .staging import ensure_staged
from .stats import block_bootstrap_mean, classify_rho
from .store import read_json, write_json_atomic

LIBRARY_FACTORS: tuple[str, ...] = (
    "liquidity.turnover_ma_20d",
    "liquidity.amount_ma_20d",
    "liquidity.turnover_std_bias_21_504",
    "liquidity.absret_amount_20d",
    "momentum.ret_21d",
    "momentum.ret_63d",
    "momentum.ret_126d",
    "momentum.alpha_125d_csi300",
    "risk.vol_21d",
    "risk.high_low_21d",
    "reversal.zjfz",
    "size.mv",
)


def _weekly(frame: pd.DataFrame) -> pd.DataFrame:
    return frame.resample("W-FRI").last()


def _weekly_rho(candidate: pd.DataFrame, other: pd.DataFrame) -> tuple[float | None, int]:
    a = _weekly(candidate)
    b = _weekly(other)
    dates = a.index.intersection(b.index)
    values: list[float] = []
    for date in dates:
        left = a.loc[date].dropna()
        right = b.loc[date].dropna()
        common = left.index.intersection(right.index)
        if len(common) < 30:
            continue
        x = left.loc[common].rank()
        y = right.loc[common].rank()
        rho = x.corr(y)
        if rho == rho:
            values.append(float(rho))
    if not values:
        return None, 0
    return float(np.mean(values)), len(values)


def compute_orthogonality(root: Path) -> dict[str, Any]:
    manifest = read_json(root / "manifest.json") or {}
    hypothesis = read_json(root / "hypothesis.json") or {}
    outcome = OutcomeSpec.from_dict(manifest.get("outcome") or {})
    factor_id = str(manifest.get("factor_id") or "")
    ensure_staged(factor_id)

    start = str((manifest.get("params") or {}).get("start") or hypothesis.get("start") or "2019-01-01")
    end = (manifest.get("params") or {}).get("end") or hypothesis.get("end")
    panel = load_panel(
        None,
        None,
        start,
        end,
        universe=outcome.universe,
        extras=(),
        use_cache=True,
    )
    candidate = price_factor_panel(factor_id, panel)

    pairs: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    for other_id in LIBRARY_FACTORS:
        if other_id == factor_id:
            continue
        try:
            other = price_factor_panel(other_id, panel)
        except Exception as exc:  # noqa: BLE001
            skipped.append({"factor_id": other_id, "reason": f"{type(exc).__name__}: {exc}"})
            continue
        rho, n_obs = _weekly_rho(candidate, other)
        if rho is None:
            skipped.append({"factor_id": other_id, "reason": "no overlapping weekly observations"})
            continue
        pairs.append({"factor_id": other_id, "rho": round(rho, 6), "n_weeks": n_obs})

    if not pairs:
        raise RuntimeError("orthogonality: no library factor produced a usable correlation")

    best = max(pairs, key=lambda item: abs(float(item["rho"])))
    # Bootstrap the best pair's weekly series for a CI.  Recompute the series to
    # avoid storing it in the result pairs.
    best_other = price_factor_panel(str(best["factor_id"]), panel)
    a = _weekly(candidate)
    b = _weekly(best_other)
    dates = a.index.intersection(b.index)
    values: list[float] = []
    for date in dates:
        left = a.loc[date].dropna()
        right = b.loc[date].dropna()
        common = left.index.intersection(right.index)
        if len(common) < 30:
            continue
        rho = left.loc[common].rank().corr(right.loc[common].rank())
        if rho == rho:
            values.append(float(rho))
    mean, ci_low, ci_high = block_bootstrap_mean(values, block=4)
    thresholds = (read_json(config.GATE_POLICY_PATH) or {}).get("thresholds") or {}
    hard = float(thresholds.get("max_abs_rho_hard", 0.9))
    warn = float(thresholds.get("max_abs_rho_warn", 0.7))
    decision = classify_rho(ci_low, ci_high, hard=hard, warn=warn)

    snapshot = {
        "schema_version": config.SCHEMA_VERSION,
        "created_at": config.now_iso(),
        "universe": outcome.universe,
        "start": start,
        "end": end,
        "factors": [
            {"factor_id": item["factor_id"], "source": "price_factor_panel"}
            for item in pairs
        ],
        "skipped": skipped,
    }
    write_json_atomic(root / "library_snapshot.json", snapshot)
    write_json_atomic(config.LAB_ROOT / "library_snapshot.json", snapshot)

    result = {
        "schema_version": config.SCHEMA_VERSION,
        "exp_id": root.name,
        "candidate": factor_id,
        "method": "weekly cross-sectional Spearman; block bootstrap block=4 weeks; exposure only",
        "rho_exposure": round(mean, 6) if mean is not None else None,
        "rho_return": None,
        "ci_low": round(ci_low, 6) if ci_low is not None else None,
        "ci_high": round(ci_high, 6) if ci_high is not None else None,
        "max_abs_rho": round(abs(float(best["rho"])), 6),
        "nearest": best["factor_id"],
        "decision": decision,
        "pairs": sorted(pairs, key=lambda item: abs(float(item["rho"])), reverse=True),
        "skipped": skipped,
        "note": "rho_return pending; decision uses the exposure interval and is labeled exposure-only.",
        "created_at": config.now_iso(),
    }
    write_json_atomic(root / "orthogonality.json", result)
    return result

def _policy_thresholds() -> tuple[float, float]:
    policy = read_json(config.GATE_POLICY_PATH) or {}
    thresholds = policy.get("thresholds") or {}
    return float(thresholds.get("max_abs_rho_hard", 0.9)), float(thresholds.get("max_abs_rho_warn", 0.7))


def _rho_series(candidate, other):
    a = _weekly(candidate)
    b = _weekly(other)
    dates = a.index.intersection(b.index)
    values = []
    for date in dates:
        left = a.loc[date].dropna()
        right = b.loc[date].dropna()
        common = left.index.intersection(right.index)
        if len(common) < 30:
            continue
        rho = left.loc[common].rank().corr(right.loc[common].rank())
        if rho == rho:
            values.append(float(rho))
    return values


def compute_batch_orthogonality(roots):
    """Compute exposure correlations for many experiments with one panel load."""
    roots = [Path(root) for root in roots]
    if not roots:
        return []
    first_manifest = read_json(roots[0] / "manifest.json") or {}
    outcome = OutcomeSpec.from_dict(first_manifest.get("outcome") or {})
    start = str((first_manifest.get("params") or {}).get("start") or "2019-01-01")
    end = (first_manifest.get("params") or {}).get("end")
    panel = load_panel(None, None, start, end, universe=outcome.universe, extras=(), use_cache=True)
    hard, warn = _policy_thresholds()

    library = {}
    for other_id in LIBRARY_FACTORS:
        try:
            library[other_id] = price_factor_panel(other_id, panel)
        except Exception:
            continue

    results = []
    last_snapshot = None
    for root in roots:
        manifest = read_json(root / "manifest.json") or {}
        factor_id = str(manifest.get("factor_id") or "")
        ensure_staged(factor_id)
        candidate = price_factor_panel(factor_id, panel)
        pairs = []
        skipped = []
        for other_id, other_panel in library.items():
            if other_id == factor_id:
                continue
            rho, n_obs = _weekly_rho(candidate, other_panel)
            if rho is None:
                skipped.append({"factor_id": other_id, "reason": "no overlapping weekly observations"})
                continue
            pairs.append({"factor_id": other_id, "rho": round(rho, 6), "n_weeks": n_obs})
        if not pairs:
            results.append({"exp_id": root.name, "factor_id": factor_id, "error": "no usable library factor"})
            continue
        best = max(pairs, key=lambda item: abs(float(item["rho"])))
        best_panel = library[str(best["factor_id"])]
        mean, ci_low, ci_high = block_bootstrap_mean(_rho_series(candidate, best_panel), block=4)
        decision = classify_rho(ci_low, ci_high, hard=hard, warn=warn)
        snapshot = {
            "schema_version": config.SCHEMA_VERSION,
            "created_at": config.now_iso(),
            "universe": outcome.universe,
            "start": start,
            "end": end,
            "factors": [{"factor_id": item["factor_id"], "source": "price_factor_panel"} for item in pairs],
            "skipped": skipped,
        }
        write_json_atomic(root / "library_snapshot.json", snapshot)
        last_snapshot = snapshot
        result = {
            "schema_version": config.SCHEMA_VERSION,
            "exp_id": root.name,
            "candidate": factor_id,
            "method": "weekly cross-sectional Spearman; block bootstrap block=4 weeks; exposure only",
            "rho_exposure": round(mean, 6) if mean is not None else None,
            "rho_return": None,
            "ci_low": round(ci_low, 6) if ci_low is not None else None,
            "ci_high": round(ci_high, 6) if ci_high is not None else None,
            "max_abs_rho": round(abs(float(best["rho"])), 6),
            "nearest": best["factor_id"],
            "decision": decision,
            "pairs": sorted(pairs, key=lambda item: abs(float(item["rho"])), reverse=True),
            "skipped": skipped,
            "note": "rho_return pending; decision uses the exposure interval and is labeled exposure-only.",
            "created_at": config.now_iso(),
        }
        write_json_atomic(root / "orthogonality.json", result)
        results.append(result)
    if last_snapshot is not None:
        write_json_atomic(config.LAB_ROOT / "library_snapshot.json", last_snapshot)
    return results
