"""Distill a linear ML signal into a DSL factor artifact (P2).

v1 only supports the implemented ridge/linear models.  Non-linear model
wrappers (`kind="model"`) require a Runner extension and are intentionally out
of scope for this phase; distill fidelity is a v2 check.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from factor_lab.alpha.factor_library import write_factor_artifact
from factor_lab.alpha.validation import validate
from factor_lab.alpha.compiler import compile_expression
from factor_lab.alpha.governance import policy_snapshot
from factor_lab.core.store import write_json_atomic


EXPORT_SCHEMA = "factor_lab.alpha.ml_export.v1"


def _term(feature: str, weight: float) -> tuple[str, float]:
    magnitude = abs(float(weight))
    term = f"mul({magnitude:.10f}, rank({feature}))"
    return term, float(weight)


def _projection_weights(fidelity_data: dict[str, Any]) -> dict[str, float] | None:
    """Fit rank-space weights to the OOS model score (distillation projection)."""
    import numpy as np
    import pandas as pd

    x = fidelity_data.get("x")
    predictions = fidelity_data.get("predictions")
    dates = fidelity_data.get("dates")
    symbols = fidelity_data.get("symbols")
    features = [str(item) for item in (fidelity_data.get("features") or [])]
    if x is None or predictions is None or not features:
        return None
    try:
        frame = pd.DataFrame(np.asarray(x, dtype=float), columns=features)
        frame["_pred"] = np.asarray(predictions, dtype=float)
        frame["_date"] = pd.to_datetime(pd.Series(dates))
        frame["_symbol"] = [str(item) for item in symbols]
        frame = frame.dropna()
        if len(frame) < max(100, len(features) * 20):
            return None
        y = frame.groupby("_date")["_pred"].rank(pct=True).to_numpy(dtype=float)
        columns = []
        for feature in features:
            values = frame.groupby("_date")[feature].rank(pct=True).to_numpy(dtype=float)
            columns.append(values)
        design = np.column_stack(columns)
        coef, *_ = np.linalg.lstsq(design, y, rcond=None)
        return {feature: float(value) for feature, value in zip(features, coef)}
    except Exception:  # noqa: BLE001
        return None


def distill_expression(model_card: dict[str, Any], fidelity_data: dict[str, Any] | None = None) -> dict[str, Any]:
    features = [str(item) for item in (model_card.get("features") or [])]
    importance = model_card.get("standardized_feature_importance") or model_card.get("feature_importance") or {}
    projected = _projection_weights(fidelity_data or {})
    if projected:
        importance = projected
        method = "oos_rank_projection"
    else:
        method = "model_card_coefficients"
    terms = []
    for feature in features:
        weight = importance.get(feature)
        if weight is None or abs(float(weight)) < 1e-12:
            continue
        term, signed = _term(feature, float(weight))
        if term:
            terms.append((term, signed, feature, float(weight)))
    if not terms:
        raise ValueError("model card has no feature coefficients to distill")
    expression = terms[0][0]
    if terms[0][1] < 0:
        expression = f"neg({expression})"
    for term, signed, _feature, _weight in terms[1:]:
        if signed >= 0:
            expression = f"add({expression}, {term})"
        else:
            expression = f"sub({expression}, {term})"
    expression = f"rank({expression})"
    report = validate(expression)
    if not report.ok:
        raise ValueError(f"distilled expression is invalid: {report.issues}")
    return {
        "expression": expression,
        "weights": {feature: weight for _term, _signed, feature, weight in terms},
        "method": method,
        "validation": report.to_dict(),
    }


def distill_fidelity(result: dict[str, Any], dataset: Any, *, expression: str | None = None) -> dict[str, Any]:
    """Compare the distilled DSL factor with the OOS model score by date."""
    import numpy as np
    import pandas as pd

    data = result.get("_fidelity_data") or {}
    predictions = data.get("predictions")
    dates = data.get("dates")
    symbols = data.get("symbols")
    if predictions is None or dates is None or symbols is None:
        return {"schema": "factor_lab.alpha.distill_fidelity.v1", "available": False, "reason": "no in-memory OOS predictions"}
    model_card = result.get("model_card") or {}
    distilled = {"expression": expression} if expression else distill_expression(model_card, result.get("_fidelity_data"))
    factor, _ = compile_expression(str(distilled["expression"]), dataset.context)
    pred = pd.DataFrame({"date": pd.to_datetime(np.asarray(dates)), "symbol": [str(item) for item in symbols], "pred": np.asarray(predictions, dtype=float)})
    pred_wide = pred.pivot(index="date", columns="symbol", values="pred")
    common_dates = pred_wide.index.intersection(factor.index)
    corr = []
    for date in common_dates:
        pair = pd.concat([pred_wide.loc[date].rename("p"), factor.loc[date].rename("f")], axis=1).dropna()
        if len(pair) >= 10:
            value = pair["p"].rank().corr(pair["f"].rank())
            if np.isfinite(value):
                corr.append(float(value))
    value = float(np.mean(corr)) if corr else None
    return {
        "schema": "factor_lab.alpha.distill_fidelity.v1",
        "available": True,
        "n_dates": len(corr),
        "mean_rank_corr": value,
        "passed": bool(value is not None and abs(value) >= 0.95),
        "threshold": 0.95,
        "expression": str(distilled["expression"]),
    }


def export_ml_factor(
    result: dict[str, Any],
    *,
    factor_id: str,
    family: str = "ml_signal",
    library_root: str | Path | None = None,
    out_path: str | Path | None = None,
    dataset: Any | None = None,
    require_fidelity: bool = False,
    campaign_id: str = "",
    distill_mode: str | None = None,
) -> dict[str, Any]:
    model_card = result.get("model_card") or {}
    mode = str(distill_mode or (result.get("plan") or {}).get("distill_mode") or "projection")
    if mode not in {"projection", "model_card"}:
        raise ValueError("distill_mode must be projection or model_card")
    distilled = (
        distill_expression(model_card, None)
        if mode == "model_card"
        else distill_expression(model_card, result.get("_fidelity_data"))
    )
    fidelity = distill_fidelity(result, dataset, expression=distilled["expression"]) if dataset is not None else None
    if require_fidelity and fidelity is not None and not fidelity.get("passed"):
        raise ValueError(f"distill fidelity gate failed: {fidelity}")
    plan = result.get("plan") or {}
    spec = {
        "kind": "ml_distilled",
        "mechanism": "linear ML signal distilled to DSL rank combination",
        "source_campaign": campaign_id or plan.get("plan_id"),
        "campaign_id": campaign_id or None,
        "policy_hash": policy_snapshot().get("policy_hash"),
        "model_family": model_card.get("model_family"),
        "features": model_card.get("features"),
        "evaluation_spec": result.get("evaluation_spec"),
        "distilled_weights": distilled["weights"],
        "distill_mode": mode,
        "fidelity_policy": "report_only" if not require_fidelity else "gate_0_95",
        "standardized_weights": (model_card.get("standardized_feature_importance") or {}),
        "holdout_confirmed": False,
        "venue": "P2 ML execution",
    }
    artifact = write_factor_artifact(
        factor_id=factor_id,
        family=family,
        spec=spec,
        expression=distilled["expression"],
        source="ml_export",
        library_root=library_root,
    )
    payload = {
        "schema": EXPORT_SCHEMA,
        "factor_id": artifact.factor_id,
        "family": artifact.family,
        "version": artifact.version,
        "expression": distilled["expression"],
        "weights": distilled["weights"],
        "standardized_weights": model_card.get("standardized_feature_importance") or {},
        "validation": distilled["validation"],
        "fidelity": fidelity,
        "manifest_path": str(artifact.path),
    }
    if out_path is not None:
        target = Path(out_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        write_json_atomic(target, payload)
        payload["path"] = str(target)
    return payload
