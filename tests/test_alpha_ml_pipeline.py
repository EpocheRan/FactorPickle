"""P2 ML execution: plan loading, admission, distillation."""

from __future__ import annotations

import pytest

from factor_lab.alpha.ml_admission import admit_ml_result
from factor_lab.alpha.ml_branch import plan_from_dict
from factor_lab.alpha.ml_export import distill_expression
from factor_lab.alpha.validation import validate


def _result(*, low: float = -0.2, pb: float = -0.3, coverage: float = 0.95, folds: int = 3) -> dict:
    return {
        "plan": {
            "plan_id": "MLP-TEST",
            "model_family": "ridge",
            "cv_scheme": "purged_walk_forward",
            "embargo_days": 5,
            "target_horizon": 5,
            "features": ["low", "pb_mrq", "turnover"],
        },
        "metrics": {"oos_rank_ic_mean": 0.04, "oos_rank_ic_t": 3.0, "n_oos_dates": 300},
        "cv": {
            "scheme": "purged_walk_forward",
            "embargo_days": 5,
            "splits": [{"ic_mean": 0.02} for _ in range(folds)],
        },
        "model_card": {
            "plan_id": "MLP-TEST",
            "model_family": "ridge",
            "features": ["low", "pb_mrq", "turnover"],
            "feature_importance": {"low": low, "pb_mrq": pb, "turnover": 0.01},
        },
        "data_bundle": {"n_dates": 100, "n_symbols": 100, "n_finite_rows": int(100 * 100 * coverage)},
        "monitor": {"passed": True, "warnings": []},
        "evaluation_spec": {"version": "ml-runtime-v1", "comparable_with_cross_section_core": False},
    }


def test_plan_from_dict_requires_features() -> None:
    plan = plan_from_dict({"features": ["low", "pb_mrq"], "target_horizon": 5, "model_family": "ridge"})
    assert plan.features == ["low", "pb_mrq"]
    with pytest.raises(ValueError):
        plan_from_dict({"features": []})


def test_admission_passes_when_all_checks_hold() -> None:
    result = _result()
    admission = admit_ml_result(result, expected_signs={"low": "negative", "pb_mrq": "negative"})
    assert admission["passed"] is True
    assert admission["blocked_reasons"] == []
    assert admission["metrics"]["positive_folds"] == 3


def test_admission_rejects_wrong_sign_or_low_coverage() -> None:
    wrong_sign = _result(low=0.01)
    assert admit_ml_result(wrong_sign, expected_signs={"low": "negative"})["passed"] is False
    low_coverage = _result(coverage=0.80)
    assert admit_ml_result(low_coverage)["passed"] is False


def test_distill_linear_model_to_valid_dsl() -> None:
    result = _result()
    distilled = distill_expression(result["model_card"])
    assert validate(distilled["expression"]).ok is True
    assert "rank(low)" in distilled["expression"]
    assert "rank(pb_mrq)" in distilled["expression"]
    assert distilled["weights"]["low"] == -0.2
