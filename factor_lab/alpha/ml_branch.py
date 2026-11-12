"""Non-deep-learning ML branch for the Alpha research engine.

This is a deliberately small, deterministic and auditable implementation:
- plans are explicit;
- data bundles carry features / target / masks / PIT;
- validation uses purged walk-forward splits with embargo;
- models are ridge / linear baselines, not deep learning;
- signal evidence is long-short research evidence, not automatic capital.

The daily rank-IC metric is imported from ``factor_metrics`` (one-way
dependency on the calibrated core); CV scheme, embargo, model fitting and the
ML monitor remain owned by this deterministic runtime.
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

from factor_metrics import rank_ic_frame

from factor_lab.alpha.data import AlphaDataset
from factor_lab.alpha.universe import UniversePolicy, universe_summary
from factor_lab.core import config
from factor_lab.core.store import write_json_atomic


ML_PLAN_SCHEMA = "factor_lab.alpha.ml_plan.v1"
ML_RESULT_SCHEMA = "factor_lab.alpha.ml_result.v1"
MODEL_CARD_SCHEMA = "factor_lab.alpha.model_card.v1"


@dataclass(frozen=True)
class MLPlan:
    plan_id: str
    hypothesis: str
    features: list[str]
    target_horizon: int
    model_family: str = "ridge"
    cv_scheme: str = "purged_walk_forward"
    embargo_days: int = 5
    n_splits: int = 4
    ridge_alpha: float = 1.0
    baseline: str = "zero"
    created_at: str = field(default_factory=config.now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": ML_PLAN_SCHEMA,
            "plan_id": self.plan_id,
            "hypothesis": self.hypothesis,
            "features": list(self.features),
            "target_horizon": self.target_horizon,
            "model_family": self.model_family,
            "cv_scheme": self.cv_scheme,
            "embargo_days": self.embargo_days,
            "n_splits": self.n_splits,
            "ridge_alpha": self.ridge_alpha,
            "baseline": self.baseline,
            "created_at": self.created_at,
        }


def make_ml_plan(
    hypothesis: str,
    features: Iterable[str],
    *,
    target_horizon: int = 5,
    model_family: str = "ridge",
    embargo_days: int = 5,
    n_splits: int = 4,
    ridge_alpha: float = 1.0,
) -> MLPlan:
    fields = [str(item) for item in features if str(item).strip()]
    if not fields:
        raise ValueError("ML plan requires at least one feature")
    if model_family not in {"ridge", "zero"}:
        raise ValueError("only non-DL baselines are implemented: ridge, zero")
    if target_horizon < 1:
        raise ValueError("target_horizon must be positive")
    return MLPlan(
        plan_id=f"MLP-{uuid.uuid4().hex[:10]}",
        hypothesis=str(hypothesis),
        features=fields,
        target_horizon=int(target_horizon),
        model_family=model_family,
        embargo_days=max(0, int(embargo_days)),
        n_splits=max(1, int(n_splits)),
        ridge_alpha=float(ridge_alpha),
    )


def _stack_panel(frame: pd.DataFrame) -> tuple[np.ndarray, list[pd.Timestamp], list[str]]:
    values = frame.to_numpy(dtype=float)
    dates = list(frame.index)
    symbols = [str(item) for item in frame.columns]
    return values, dates, symbols


def validate_ml_features(features: list[str], available_fields: Iterable[str] | None = None) -> dict[str, Any]:
    """Validate ML features against the panel and expose the expanded-axis catalog.

    ML features remain explicit (no auto-injection), but this check makes a
    missing expanded field fail with the catalog hint instead of an opaque
    tensor-shape error.
    """
    from factor_lab.alpha.data_axes import ml_feature_catalog

    catalog = ml_feature_catalog()
    expanded = set(catalog.get("expanded_fields") or [])
    names = [str(item) for item in features if str(item).strip()]
    missing = sorted({name for name in names if available_fields is not None and name not in set(available_fields)})
    if missing:
        raise ValueError(f"unknown ML features: {missing}; expanded fields available: {sorted(expanded)}")
    return {"features": names, "expanded_features": sorted(set(names) & expanded), "catalog": catalog}


def plan_from_dict(payload: dict[str, Any]) -> MLPlan:
    """Load a pre-registered ML plan from JSON (ML Planner output)."""
    if not isinstance(payload, dict):
        raise ValueError("ML plan must be a JSON object")
    features = [str(item) for item in (payload.get("features") or []) if str(item).strip()]
    if not features:
        raise ValueError("ML plan requires features")
    return MLPlan(
        plan_id=str(payload.get("plan_id") or f"MLP-{uuid.uuid4().hex[:10]}"),
        hypothesis=str(payload.get("hypothesis") or ""),
        features=features,
        target_horizon=int(payload.get("target_horizon") or payload.get("horizon") or 5),
        model_family=str(payload.get("model_family") or "ridge"),
        cv_scheme=str(payload.get("cv_scheme") or "purged_walk_forward"),
        embargo_days=int(payload.get("embargo_days") or 5),
        n_splits=int(payload.get("n_splits") or 3),
        ridge_alpha=float(payload.get("ridge_alpha") or 1.0),
        baseline=str(payload.get("baseline") or "zero"),
        created_at=str(payload.get("created_at") or config.now_iso()),
    )


def build_ml_bundle(dataset: AlphaDataset, plan: MLPlan, *, max_rows: int | None = None, universe: pd.DataFrame | None = None) -> dict[str, Any]:
    """Build a date-aligned feature/label bundle with PIT masks."""
    context = dataset.context
    missing = [name for name in plan.features if name not in context.fields]
    if missing:
        raise ValueError(f"unknown ML features: {missing}")
    # Rows are dates; columns are symbols. Stack each feature into a 3D tensor
    # and flatten to (date, symbol, feature) for deterministic fitting.
    feature_tensor = np.stack(
        [context.fields[name].to_numpy(dtype=float) for name in plan.features],
        axis=-1,
    )
    close = context.close
    target = close.shift(-int(plan.target_horizon)) / close - 1.0
    target_values = target.to_numpy(dtype=float)
    n_dates, n_symbols, n_features = feature_tensor.shape
    if max_rows is not None:
        n_dates = min(n_dates, int(max_rows))
        feature_tensor = feature_tensor[:n_dates]
        target_values = target_values[:n_dates]
    flat_x = feature_tensor.reshape(n_dates * n_symbols, n_features)
    flat_y = target_values.reshape(n_dates * n_symbols)
    date_index = np.repeat(np.arange(n_dates), n_symbols)
    symbol_index = np.tile(np.arange(n_symbols), n_dates)
    finite = np.isfinite(flat_x).all(axis=1) & np.isfinite(flat_y)
    if universe is not None:
        eligible = (
            universe.reindex(index=context.close.index, columns=context.close.columns)
            .fillna(False)
            .to_numpy(dtype=bool)
        )
        eligible_flat = eligible[:n_dates].reshape(-1)
    else:
        eligible_flat = np.ones(n_dates * n_symbols, dtype=bool)
    finite = finite & eligible_flat
    n_eligible_rows = int(eligible_flat.sum())
    bundle = {
        "schema": "factor_lab.alpha.ml_data_bundle.v1",
        "plan_id": plan.plan_id,
        "features": list(plan.features),
        "target": f"forward_{int(plan.target_horizon)}d_return",
        "target_horizon": int(plan.target_horizon),
        "dates": [str(item.date()) for item in context.close.index[:n_dates]],
        "symbols": [str(item) for item in context.close.columns],
        "n_dates": int(n_dates),
        "n_symbols": int(n_symbols),
        "n_features": int(n_features),
        "n_finite_rows": int(finite.sum()),
        "n_eligible_rows": n_eligible_rows,
        "masks": {
            "train": {"start": str(context.close.index[0].date()), "end": str(context.close.index[max(0, n_dates - 1)].date())},
            "embargo_days": int(plan.embargo_days),
            "scheme": plan.cv_scheme,
        },
        "pit": {
            "required": True,
            "available_at": "adapter data manifest",
            "target_is_forward": True,
        },
        "arrays": {
            "x": flat_x,
            "y": flat_y,
            "dates": date_index,
            "symbols": symbol_index,
            "finite": finite,
        },
        "data_manifest": dataset.data_manifest,
    }
    return bundle


def purged_walk_forward_splits(
    n_dates: int,
    *,
    n_splits: int = 4,
    embargo_days: int = 5,
) -> list[tuple[np.ndarray, np.ndarray]]:
    """Expanding train windows with a gap between train and test."""
    if n_dates < 3:
        raise ValueError("need at least 3 dates")
    n_splits = max(1, min(int(n_splits), max(1, n_dates // 3)))
    test_size = max(1, n_dates // (n_splits + 1))
    splits: list[tuple[np.ndarray, np.ndarray]] = []
    for index in range(n_splits):
        test_end = n_dates - (n_splits - index - 1) * test_size
        test_start = max(0, test_end - test_size)
        train_end = max(1, test_start - int(embargo_days))
        train = np.arange(0, train_end)
        test = np.arange(test_start, min(n_dates, test_end))
        if len(train) and len(test):
            splits.append((train, test))
    return splits


def _fit_ridge(x: np.ndarray, y: np.ndarray, alpha: float) -> tuple[np.ndarray, float]:
    if x.size == 0:
        return np.zeros(x.shape[1]), 0.0
    mean = x.mean(axis=0)
    scale = x.std(axis=0)
    scale[scale == 0] = 1.0
    z = (x - mean) / scale
    y_mean = float(y.mean())
    gram = z.T @ z + float(alpha) * np.eye(z.shape[1])
    coef = np.linalg.solve(gram, z.T @ (y - y_mean))
    return coef / scale, y_mean - float((mean / scale) @ coef)


def _rank_ic(
    predictions: np.ndarray,
    target: np.ndarray,
    groups: np.ndarray,
    symbols: np.ndarray,
    *,
    min_names: int = 5,
) -> list[float]:
    """Daily cross-sectional rank IC using the calibrated core implementation.

    The ML runtime keeps its own purged CV / embargo / model fitting, but the
    rank-IC formula is the shared research-calibrated one (one-way dependency:
    ``factor_lab -> factor_metrics``).
    """
    frame = pd.DataFrame(
        {
            "date": np.asarray(groups),
            "symbol": np.asarray(symbols),
            "pred": np.asarray(predictions, dtype="float64"),
            "truth": np.asarray(target, dtype="float64"),
        }
    )
    pred_wide = frame.pivot(index="date", columns="symbol", values="pred")
    truth_wide = frame.pivot(index="date", columns="symbol", values="truth")
    series = rank_ic_frame(pred_wide, truth_wide, min_names=int(min_names))
    clean = series.replace([np.inf, -np.inf], np.nan).dropna()
    return [float(value) for value in clean.tolist()]


def run_ml_experiment(
    dataset: AlphaDataset,
    plan: MLPlan,
    *,
    out_dir: str | Path | None = None,
    universe: pd.DataFrame | None = None,
    universe_policy: UniversePolicy | None = None,
) -> dict[str, Any]:
    validate_ml_features(plan.features, dataset.context.fields.keys())
    bundle = build_ml_bundle(dataset, plan, universe=universe)
    x = bundle["arrays"]["x"]
    y = bundle["arrays"]["y"]
    finite = bundle["arrays"]["finite"]
    dates = bundle["arrays"]["dates"]
    symbols = bundle["arrays"]["symbols"]
    flat_x = x[finite]
    flat_y = y[finite]
    flat_dates = dates[finite]
    flat_symbols = symbols[finite]
    if len(flat_y) == 0:
        raise ValueError("ML bundle has no finite rows")
    purge_days = max(int(plan.embargo_days), int(plan.target_horizon))
    splits = purged_walk_forward_splits(
        int(bundle["n_dates"]),
        n_splits=plan.n_splits,
        embargo_days=purge_days,
    )
    oos_predictions: list[np.ndarray] = []
    oos_targets: list[np.ndarray] = []
    oos_dates: list[np.ndarray] = []
    oos_symbols: list[np.ndarray] = []
    fold_rows: list[dict[str, Any]] = []
    last_coef = np.zeros(flat_x.shape[1])
    for fold, (train_dates, test_dates) in enumerate(splits, start=1):
        train_mask = np.isin(flat_dates, train_dates)
        test_mask = np.isin(flat_dates, test_dates)
        if not train_mask.any() or not test_mask.any():
            continue
        if plan.model_family == "zero":
            coef = np.zeros(flat_x.shape[1])
            intercept = 0.0
        else:
            coef, intercept = _fit_ridge(flat_x[train_mask], flat_y[train_mask], plan.ridge_alpha)
        last_coef = np.asarray(coef)
        pred = flat_x[test_mask] @ coef + intercept
        truth = flat_y[test_mask]
        fold_dates = flat_dates[test_mask]
        fold_symbols = flat_symbols[test_mask]
        ics = _rank_ic(pred, truth, fold_dates, fold_symbols)
        fold_rows.append(
            {
                "fold": fold,
                "n_train": int(train_mask.sum()),
                "n_test": int(test_mask.sum()),
                "ic_mean": float(np.mean(ics)) if ics else None,
                "test_dates": int(len(np.unique(fold_dates))),
            }
        )
        oos_predictions.append(pred)
        oos_targets.append(truth)
        oos_dates.append(fold_dates)
        oos_symbols.append(fold_symbols)
    if not fold_rows:
        raise ValueError("no valid purged CV folds")
    predictions = np.concatenate(oos_predictions)
    targets = np.concatenate(oos_targets)
    groups = np.concatenate(oos_dates)
    symbols_oos = np.concatenate(oos_symbols)
    oos_ics = _rank_ic(predictions, targets, groups, symbols_oos)
    mean_ic = float(np.mean(oos_ics)) if oos_ics else 0.0
    std_ic = float(np.std(oos_ics, ddof=1)) if len(oos_ics) > 1 else 0.0
    t_stat = mean_ic / (std_ic / math.sqrt(len(oos_ics))) if std_ic > 0 else 0.0
    model_card = {
        "schema": MODEL_CARD_SCHEMA,
        "plan_id": plan.plan_id,
        "evaluation_spec_version": "ml-runtime-v1",
        "model_family": plan.model_family,
        "features": list(plan.features),
        "feature_importance": {name: float(value) for name, value in zip(plan.features, last_coef)},
        "cv_scheme": plan.cv_scheme,
        "embargo_days": purge_days,
        "n_splits": len(fold_rows),
        "target": bundle["target"],
        "target_horizon": plan.target_horizon,
        "created_at": config.now_iso(),
        "limitations": [
            "linear ridge baseline; non-linear models must use the same purged validation",
            "signal evidence is research evidence, not deployment approval",
        ],
    }
    result = {
        "schema": ML_RESULT_SCHEMA,
        "plan": plan.to_dict(),
        "data_bundle": {key: value for key, value in bundle.items() if key != "arrays"},
        "cv": {"splits": fold_rows, "scheme": plan.cv_scheme, "embargo_days": purge_days},
        "metrics": {
            "oos_rank_ic_mean": mean_ic,
            "oos_rank_ic_std": std_ic,
            "oos_rank_ic_t": t_stat,
            "n_oos_dates": int(len(oos_ics)),
            "n_oos_rows": int(len(targets)),
        },
        "model_card": model_card,
        "created_at": config.now_iso(),
        "evaluation_spec": {
            "version": "ml-runtime-v1",
            "metric": "daily_rank_ic",
            "rank_ic_source": "factor_metrics.rank_ic_frame",
            "depends_on": ["factor_metrics"],
            "cv_scheme": plan.cv_scheme,
            "target_horizon": int(plan.target_horizon),
            "embargo_days": int(plan.embargo_days),
            "model_family": plan.model_family,
            "comparable_with_cross_section_core": False,
        },
        "universe": universe_summary(universe, universe_policy),
    }
    result["monitor"] = monitor_ml_result(result)
    if out_dir is not None:
        root = Path(out_dir)
        root.mkdir(parents=True, exist_ok=True)
        write_json_atomic(root / f"{plan.plan_id}.plan.json", plan.to_dict())
        write_json_atomic(root / f"{plan.plan_id}.bundle.json", {k: v for k, v in bundle.items() if k != "arrays"})
        write_json_atomic(root / f"{plan.plan_id}.result.json", result)
        write_json_atomic(root / f"{plan.plan_id}.model_card.json", model_card)
    return result


def monitor_ml_result(result: dict[str, Any]) -> dict[str, Any]:
    """Deterministic ML Monitor; never upgrades evidence by itself."""
    metrics = result.get("metrics") or {}
    cv = result.get("cv") or {}
    warnings: list[str] = []
    t_stat = metrics.get("oos_rank_ic_t")
    if t_stat is None or abs(float(t_stat)) < 2.0:
        warnings.append("oos rank IC t below 2")
    if int(metrics.get("n_oos_dates") or 0) < 20:
        warnings.append("too few OOS dates")
    embargo = int((cv or {}).get("embargo_days") or (result.get("plan") or {}).get("embargo_days") or 0)
    horizon = int((result.get("plan") or {}).get("target_horizon") or 0)
    if embargo < horizon:
        warnings.append("embargo shorter than target horizon")
    if not result.get("model_card"):
        warnings.append("model_card missing")
    return {
        "schema": "factor_lab.alpha.ml_monitor.v1",
        "plan_id": (result.get("plan") or {}).get("plan_id"),
        "passed": not warnings,
        "warnings": warnings,
        "cv_splits": len(cv.get("splits") or []),
    }
