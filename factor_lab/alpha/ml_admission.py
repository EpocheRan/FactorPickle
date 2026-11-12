"""Deterministic pre-admission for the ML branch (P2).

Admission is not a Gate: it decides whether an ML experiment is strong enough
to be distilled into a factor artifact and evaluated by the normal Gate.  It
never overrides Gate verdicts and never touches holdout.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from factor_lab.core import config
from factor_lab.core.store import write_json_atomic


ADMISSION_SCHEMA = "factor_lab.alpha.ml_admission.v1"


def _finite(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _coverage(result: dict[str, Any]) -> float | None:
    bundle = result.get("data_bundle") or {}
    n_dates = int(bundle.get("n_dates") or 0)
    n_symbols = int(bundle.get("n_symbols") or 0)
    n_finite = int(bundle.get("n_finite_rows") or 0)
    # Coverage is finite feature/target cells over *eligible* cells, not all
    # warm-up rows: universe-ineligible/warm-up cells are not coverage failures.
    n_eligible = int(bundle.get("n_eligible_rows") or 0)
    if n_dates <= 0 or n_symbols <= 0:
        return None
    denominator = n_eligible if n_eligible > 0 else n_dates * n_symbols
    return n_finite / float(denominator)


def admit_ml_result(
    result: dict[str, Any],
    *,
    expected_signs: dict[str, str] | None = None,
    out_path: str | Path | None = None,
) -> dict[str, Any]:
    plan = result.get("plan") or {}
    metrics = result.get("metrics") or {}
    cv = result.get("cv") or {}
    model_card = result.get("model_card") or {}
    monitor = result.get("monitor") or {}
    coefs = model_card.get("feature_importance") or {}
    coverage = _coverage(result)
    bundle = result.get("data_bundle") or {}
    n_dates = int(bundle.get("n_dates") or 0)
    n_symbols = int(bundle.get("n_symbols") or 0)
    coverage_all_cells = (float(bundle.get("n_finite_rows") or 0) / float(n_dates * n_symbols)) if n_dates and n_symbols else None
    folds = cv.get("splits") or []
    fold_ics = [float(fold.get("ic_mean")) for fold in folds if _finite(fold.get("ic_mean"))]
    positive_folds = sum(1 for value in fold_ics if value > 0)
    required_positive = math.ceil(2.0 / 3.0 * len(folds)) if folds else 0
    embargo = int(cv.get("embargo_days") or plan.get("embargo_days") or 0)
    horizon = int(plan.get("target_horizon") or 0)
    checks = {
        "oos_ic_mean_ge_0_02": _finite(metrics.get("oos_rank_ic_mean")) and float(metrics["oos_rank_ic_mean"]) >= 0.02,
        "oos_ic_t_ge_2": _finite(metrics.get("oos_rank_ic_t_effective", metrics.get("oos_rank_ic_t"))) and abs(float(metrics.get("oos_rank_ic_t_effective", metrics.get("oos_rank_ic_t")))) >= 2.0,
        "folds_positive": bool(folds) and positive_folds >= required_positive,
        "coverage_ge_0_90": coverage is not None and coverage >= 0.90,
        "embargo_ge_horizon": embargo >= horizon,
        "purged_walk_forward": str(cv.get("scheme") or plan.get("cv_scheme")) == "purged_walk_forward",
        "model_card_present": bool(model_card),
        "monitor_passed": bool(monitor.get("passed")),
        "non_deep_learning": str(plan.get("model_family") or model_card.get("model_family") or "ridge") in {"ridge", "zero"},
        "expected_signs": True,
    }
    signs_checked = []
    for feature, expected in (expected_signs or {}).items():
        value = coefs.get(feature)
        if value is None:
            checks["expected_signs"] = False
            signs_checked.append({"feature": feature, "expected": expected, "value": None, "ok": False})
            continue
        ok = (float(value) < 0) if expected == "negative" else (float(value) > 0) if expected == "positive" else True
        signs_checked.append({"feature": feature, "expected": expected, "value": float(value), "ok": ok})
        checks["expected_signs"] = checks["expected_signs"] and ok
    blocked = [name for name, ok in checks.items() if not ok]
    payload = {
        "schema": ADMISSION_SCHEMA,
        "plan_id": plan.get("plan_id") or model_card.get("plan_id"),
        "passed": all(checks.values()),
        "checks": checks,
        "blocked_reasons": blocked,
        "metrics": {
            "oos_rank_ic_mean": metrics.get("oos_rank_ic_mean"),
            "oos_rank_ic_t": metrics.get("oos_rank_ic_t"),
            "oos_rank_ic_t_effective": metrics.get("oos_rank_ic_t_effective"),
            "n_oos_dates": metrics.get("n_oos_dates"),
            "positive_folds": positive_folds,
            "n_folds": len(folds),
            "coverage": coverage,
            "coverage_all_cells": coverage_all_cells,
            "coverage_definition": "finite_rows / eligible_rows",
            "embargo_days": embargo,
            "target_horizon": horizon,
        },
        "feature_signs": signs_checked,
        "standardized_feature_importance": model_card.get("standardized_feature_importance"),
        "evaluation_spec": result.get("evaluation_spec"),
        "created_at": config.now_iso(),
    }
    if out_path is not None:
        target = Path(out_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        payload["path"] = str(target)
        write_json_atomic(target, payload)
    return payload
