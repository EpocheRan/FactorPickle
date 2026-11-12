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
from factor_lab.core.store import write_json_atomic


EXPORT_SCHEMA = "factor_lab.alpha.ml_export.v1"


def _term(feature: str, weight: float) -> tuple[str, float]:
    magnitude = abs(float(weight))
    term = f"mul({magnitude:.10f}, rank({feature}))"
    return term, float(weight)


def distill_expression(model_card: dict[str, Any]) -> dict[str, Any]:
    features = [str(item) for item in (model_card.get("features") or [])]
    importance = model_card.get("feature_importance") or {}
    terms = []
    for feature in features:
        weight = importance.get(feature)
        if weight is None:
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
        "validation": report.to_dict(),
    }


def export_ml_factor(
    result: dict[str, Any],
    *,
    factor_id: str,
    family: str = "ml_signal",
    library_root: str | Path | None = None,
    out_path: str | Path | None = None,
) -> dict[str, Any]:
    model_card = result.get("model_card") or {}
    distilled = distill_expression(model_card)
    plan = result.get("plan") or {}
    spec = {
        "kind": "ml_distilled",
        "mechanism": "linear ML signal distilled to DSL rank combination",
        "source_campaign": plan.get("plan_id"),
        "model_family": model_card.get("model_family"),
        "features": model_card.get("features"),
        "evaluation_spec": result.get("evaluation_spec"),
        "distilled_weights": distilled["weights"],
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
        "validation": distilled["validation"],
        "manifest_path": str(artifact.path),
    }
    if out_path is not None:
        target = Path(out_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        write_json_atomic(target, payload)
        payload["path"] = str(target)
    return payload
