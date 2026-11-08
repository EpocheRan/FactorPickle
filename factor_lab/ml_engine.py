"""Non-Deep-Learning ML research runtime.

The LLM planner chooses an experiment spec; this module executes it with
deterministic sklearn models.  It deliberately has no neural-network model in
its zoo.

Input is a long panel:

    date | asset | y | feature_a | feature_b | ...

All splits are time-ordered walk-forward with an embargo; random KFold is not
implemented.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

ALLOWED_MODELS = {
    "ridge",
    "lasso",
    "elasticnet",
    "logistic",
    "random_forest",
    "extra_trees",
    "hist_gradient_boosting",
}
FORBIDDEN_MODELS = {
    "mlp",
    "neural_network",
    "cnn",
    "rnn",
    "lstm",
    "transformer",
    "deep_learning",
    "dqn",
}
DEFAULT_COST_BPS = 20.0


class MLExperimentError(RuntimeError):
    pass


@dataclass
class MLExperimentSpec:
    name: str
    features: list[str]
    model: str = "ridge"
    target: str = "y"
    date_col: str = "date"
    asset_col: str = "asset"
    n_splits: int = 5
    embargo: int = 5
    min_train_dates: int = 60
    cost_bps: float = DEFAULT_COST_BPS
    params: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        model = str(self.model).lower()
        if model in FORBIDDEN_MODELS:
            raise MLExperimentError(f"deep-learning model is forbidden: {self.model}")
        if model not in ALLOWED_MODELS:
            raise MLExperimentError(f"unknown model: {self.model}")
        if not self.features:
            raise MLExperimentError("features must not be empty")
        self.model = model


@dataclass
class MLResult:
    spec: MLExperimentSpec
    metrics: dict[str, Any]
    predictions: pd.DataFrame
    feature_importance: dict[str, float]
    folds: list[dict[str, Any]]
    candidate: bool
    reject_reasons: list[str]

    def to_dict(self, *, include_predictions: bool = False) -> dict[str, Any]:
        payload = {
            "schema_version": "factor_lab.ml_result.v1",
            "spec": {
                "name": self.spec.name,
                "features": self.spec.features,
                "model": self.spec.model,
                "target": self.spec.target,
                "n_splits": self.spec.n_splits,
                "embargo": self.spec.embargo,
                "min_train_dates": self.spec.min_train_dates,
                "cost_bps": self.spec.cost_bps,
                "params": self.spec.params,
            },
            "metrics": self.metrics,
            "feature_importance": self.feature_importance,
            "folds": self.folds,
            "candidate": self.candidate,
            "reject_reasons": self.reject_reasons,
        }
        if include_predictions:
            payload["predictions"] = self.predictions.to_dict("records")
        return payload


def _make_model(spec: MLExperimentSpec) -> Any:
    try:
        from sklearn.ensemble import ExtraTreesRegressor, HistGradientBoostingRegressor, RandomForestRegressor
        from sklearn.linear_model import ElasticNet, Lasso, LogisticRegression, Ridge
    except Exception as exc:  # noqa: BLE001
        raise MLExperimentError(f"scikit-learn unavailable: {exc}") from exc

    params = dict(spec.params)
    if spec.model == "ridge":
        return Ridge(**{"alpha": 1.0, **params})
    if spec.model == "lasso":
        return Lasso(**{"alpha": 0.001, "max_iter": 50_000, **params})
    if spec.model == "elasticnet":
        return ElasticNet(**{"alpha": 0.001, "l1_ratio": 0.5, "max_iter": 50_000, **params})
    if spec.model == "logistic":
        return LogisticRegression(**{"max_iter": 5_000, **params})
    if spec.model == "random_forest":
        return RandomForestRegressor(**{"n_estimators": 200, "min_samples_leaf": 20, "n_jobs": -1, "random_state": 7, **params})
    if spec.model == "extra_trees":
        return ExtraTreesRegressor(**{"n_estimators": 200, "min_samples_leaf": 20, "n_jobs": -1, "random_state": 7, **params})
    if spec.model == "hist_gradient_boosting":
        return HistGradientBoostingRegressor(**{"max_iter": 200, "learning_rate": 0.05, "max_leaf_nodes": 15, "random_state": 7, **params})
    raise MLExperimentError(f"model not implemented: {spec.model}")


def _feature_importance(model: Any, features: list[str]) -> dict[str, float]:
    values = None
    if hasattr(model, "feature_importances_"):
        values = np.asarray(model.feature_importances_, dtype=float)
    elif hasattr(model, "coef_"):
        values = np.abs(np.asarray(model.coef_, dtype=float)).ravel()
    if values is None or len(values) != len(features):
        return {}
    total = float(np.nansum(values))
    if total <= 0:
        return {name: 0.0 for name in features}
    return {name: round(float(value / total), 6) for name, value in zip(features, values)}


def _daily_rank_ic(frame: pd.DataFrame) -> pd.Series:
    values: dict[pd.Timestamp, float] = {}
    for timestamp, group in frame.groupby("date"):
        pair = group[["pred", "y"]].replace([np.inf, -np.inf], np.nan).dropna()
        if len(pair) < 10 or pair["pred"].nunique() < 3 or pair["y"].nunique() < 3:
            continue
        rho = pair["pred"].rank().corr(pair["y"].rank())
        if rho == rho:
            values[pd.Timestamp(timestamp)] = float(rho)
    return pd.Series(values).sort_index()


def _long_short(frame: pd.DataFrame, groups: int = 5, cost_bps: float = DEFAULT_COST_BPS) -> dict[str, float]:
    returns: list[float] = []
    turnovers: list[float] = []
    previous_top: set[str] = set()
    for _, group in frame.groupby("date"):
        group = group.dropna(subset=["pred", "y"])
        if len(group) < groups * 2:
            continue
        ranked = group.sort_values("pred")
        n = max(1, len(ranked) // groups)
        bottom = ranked.head(n)
        top = ranked.tail(n)
        spread = float(top["y"].mean() - bottom["y"].mean())
        returns.append(spread)
        top_set = set(top["asset"].astype(str))
        turnover = 1.0 if not previous_top else 1.0 - len(top_set & previous_top) / max(1, len(top_set))
        turnovers.append(float(turnover))
        previous_top = top_set
    if not returns:
        return {"net_spread": 0.0, "gross_spread": 0.0, "turnover": 0.0, "spread_t": 0.0, "positive_ratio": 0.0}
    arr = np.asarray(returns, dtype=float)
    turnover = float(np.mean(turnovers)) if turnovers else 0.0
    cost = 2.0 * cost_bps / 10_000.0 * turnover
    net = arr - cost
    se = float(np.std(net, ddof=1) / math.sqrt(len(net))) if len(net) > 1 else float("nan")
    return {
        "gross_spread": round(float(arr.mean()), 8),
        "net_spread": round(float(net.mean()), 8),
        "turnover": round(turnover, 6),
        "spread_t": round(float(net.mean() / se), 3) if se and se == se and se > 0 else 0.0,
        "positive_ratio": round(float((net > 0).mean()), 4),
    }


def _walk_forward_dates(dates: list[pd.Timestamp], n_splits: int, embargo: int, min_train_dates: int) -> list[tuple[list[pd.Timestamp], list[pd.Timestamp]]]:
    if len(dates) < min_train_dates + n_splits + embargo:
        raise MLExperimentError(
            f"not enough dates: {len(dates)} < min_train={min_train_dates}+splits={n_splits}+embargo={embargo}"
        )
    remaining = dates[min_train_dates:]
    fold_size = max(1, len(remaining) // n_splits)
    splits: list[tuple[list[pd.Timestamp], list[pd.Timestamp]]] = []
    for index in range(n_splits):
        start = index * fold_size
        end = len(remaining) if index == n_splits - 1 else (index + 1) * fold_size
        test_dates = remaining[start:end]
        if not test_dates:
            continue
        first_test = test_dates[0]
        train_dates = [d for d in dates if d < first_test]
        if embargo > 0:
            train_dates = train_dates[:-embargo] if len(train_dates) > embargo else []
        if len(train_dates) < 20:
            continue
        splits.append((train_dates, test_dates))
    if not splits:
        raise MLExperimentError("no valid walk-forward splits")
    return splits


def run_experiment(dataset: pd.DataFrame, spec: MLExperimentSpec) -> MLResult:
    required = {spec.date_col, spec.asset_col, spec.target, *spec.features}
    missing = sorted(required - set(dataset.columns))
    if missing:
        raise MLExperimentError(f"dataset missing columns: {missing}")
    data = dataset.copy()
    data[spec.date_col] = pd.to_datetime(data[spec.date_col]).dt.tz_localize(None).dt.normalize()
    data = data.dropna(subset=[spec.date_col, spec.asset_col, spec.target, *spec.features])
    if data.empty:
        raise MLExperimentError("dataset empty after dropping missing rows")
    data = data.sort_values([spec.date_col, spec.asset_col]).reset_index(drop=True)
    dates = sorted(pd.to_datetime(data[spec.date_col].unique()))
    splits = _walk_forward_dates(dates, spec.n_splits, spec.embargo, spec.min_train_dates)

    predictions: list[pd.DataFrame] = []
    folds: list[dict[str, Any]] = []
    for index, (train_dates, test_dates) in enumerate(splits, start=1):
        train = data[data[spec.date_col].isin(train_dates)]
        test = data[data[spec.date_col].isin(test_dates)].copy()
        if train.empty or test.empty:
            continue
        model = _make_model(spec)
        try:
            model.fit(train[spec.features], train[spec.target])
            test["pred"] = model.predict(test[spec.features])
        except Exception as exc:  # noqa: BLE001
            raise MLExperimentError(f"model fit/predict failed: {type(exc).__name__}: {exc}") from exc
        test["fold"] = index
        predictions.append(test[[spec.date_col, spec.asset_col, "pred", spec.target, "fold"]].rename(columns={spec.date_col: "date", spec.asset_col: "asset", spec.target: "y"}))
        folds.append(
            {
                "fold": index,
                "train_start": str(pd.Timestamp(train_dates[0]).date()),
                "train_end": str(pd.Timestamp(train_dates[-1]).date()),
                "test_start": str(pd.Timestamp(test_dates[0]).date()),
                "test_end": str(pd.Timestamp(test_dates[-1]).date()),
                "train_rows": int(len(train)),
                "test_rows": int(len(test)),
            }
        )
    if not predictions:
        raise MLExperimentError("no predictions produced")
    pred_frame = pd.concat(predictions, ignore_index=True)
    ic = _daily_rank_ic(pred_frame)
    ic_mean = float(ic.mean()) if len(ic) else 0.0
    ic_se = float(ic.std(ddof=1) / math.sqrt(len(ic))) if len(ic) > 1 else float("nan")
    ic_t = float(ic_mean / ic_se) if ic_se and ic_se == ic_se and ic_se > 0 else 0.0
    spreads = _long_short(pred_frame, cost_bps=spec.cost_bps)
    importance = _feature_importance(model, spec.features)
    yearly: dict[str, float] = {}
    if not pred_frame.empty:
        pred_frame["year"] = pd.to_datetime(pred_frame["date"]).dt.year
        for year, group in pred_frame.groupby("year"):
            series = _daily_rank_ic(group)
            if len(series):
                yearly[str(year)] = round(float(series.mean()), 6)
    metrics = {
        "ic_mean": round(ic_mean, 6),
        "ic_t": round(ic_t, 3),
        "ic_win": round(float((ic > 0).mean()), 4) if len(ic) else 0.0,
        "n_ic_dates": int(len(ic)),
        "yearly_ic": yearly,
        **spreads,
        "n_predictions": int(len(pred_frame)),
        "n_folds": len(folds),
        "model": spec.model,
    }
    reasons: list[str] = []
    if ic_mean <= 0.01:
        reasons.append("ic_mean<=0.01")
    if ic_t < 2.0:
        reasons.append("ic_t<2.0")
    if spreads["net_spread"] <= 0:
        reasons.append("net_spread<=0")
    if spreads["turnover"] > 0.8:
        reasons.append("turnover>0.8")
    if not importance:
        reasons.append("no_feature_importance")
    candidate = not reasons
    return MLResult(
        spec=spec,
        metrics=metrics,
        predictions=pred_frame,
        feature_importance=importance,
        folds=folds,
        candidate=candidate,
        reject_reasons=reasons,
    )


def signal_to_factor_decision(result: MLResult, *, max_corr_with_existing: float = 0.7) -> dict[str, Any]:
    """Pre-admission decision before FactorBuilder wraps an ML signal.

    Correlation is an input: the caller must supply the G0.5 result.  This
    function only encodes the rules that are independent of correlation.
    """

    reasons = list(result.reject_reasons)
    importance = result.feature_importance or {}
    stable_features = [name for name, value in importance.items() if value >= 0.01]
    if not stable_features:
        reasons.append("no_stable_feature")
    if result.metrics.get("ic_win", 0) < 0.5:
        reasons.append("ic_win<0.5")
    return {
        "schema_version": "factor_lab.signal_admission.v1",
        "candidate": not reasons,
        "reasons": reasons,
        "rules": {
            "oos_ic": "ic_mean>0.01 and ic_t>=2.0",
            "cost": "net_spread>0",
            "turnover": "turnover<=0.8",
            "interpretability": "feature_importance stable",
            "correlation": f"G0.5 max_abs_rho<={max_corr_with_existing}",
        },
        "model_card": {
            "model": result.spec.model,
            "features": result.spec.features,
            "target": result.spec.target,
            "splits": result.spec.n_splits,
            "embargo": result.spec.embargo,
            "cost_bps": result.spec.cost_bps,
        },
    }
