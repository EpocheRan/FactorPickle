"""ML must consume the expanded P0/P1 fields as ordinary panel features."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from factor_lab.alpha.compiler import AlphaContext
from factor_lab.alpha.data import AlphaDataset
from factor_lab.alpha.data_axes import ml_feature_catalog
from factor_lab.alpha.ml_branch import build_ml_bundle, make_ml_plan, validate_ml_features


def _dataset() -> AlphaDataset:
    dates = pd.date_range("2024-01-01", periods=24, freq="B")
    symbols = ["A", "B", "C"]
    rng = np.random.default_rng(7)
    close = pd.DataFrame(100 + rng.normal(size=(24, 3)).cumsum(axis=0), index=dates, columns=symbols)
    fields = {
        "margin_rzye": pd.DataFrame(rng.random((24, 3)) * 1e8, index=dates, columns=symbols),
        "mf_net_amount": pd.DataFrame(rng.normal(size=(24, 3)), index=dates, columns=symbols),
        "block_amount": pd.DataFrame(np.abs(rng.normal(size=(24, 3))), index=dates, columns=symbols),
        "top_net_amount": pd.DataFrame(rng.normal(size=(24, 3)), index=dates, columns=symbols),
    }
    context = AlphaContext(close=close, fields=fields, metadata={"symbols": 3})
    return AlphaDataset(context=context, codes=symbols, start=str(dates[0].date()), end=str(dates[-1].date()), warmup_days=0, data_manifest={})


def test_ml_feature_catalog_contains_expanded_axes() -> None:
    catalog = ml_feature_catalog()
    expanded = set(catalog["expanded_fields"])
    assert {"margin_rzye", "mf_net_amount", "block_amount", "top_net_amount", "holder_chg", "rep_amount"} <= expanded


def test_validate_ml_features_accepts_expanded_and_rejects_unknown() -> None:
    report = validate_ml_features(["margin_rzye", "block_amount"], available_fields=["margin_rzye", "block_amount"])
    assert set(report["expanded_features"]) == {"margin_rzye", "block_amount"}
    with pytest.raises(ValueError, match="unknown ML features"):
        validate_ml_features(["not_a_field"], available_fields=["margin_rzye"])


def test_build_ml_bundle_consumes_expanded_feature_columns() -> None:
    dataset = _dataset()
    plan = make_ml_plan(
        "expanded-axis ML smoke",
        ["margin_rzye", "mf_net_amount", "block_amount"],
        target_horizon=5,
        n_splits=2,
        embargo_days=5,
    )
    bundle = build_ml_bundle(dataset, plan)
    assert bundle["features"] == ["margin_rzye", "mf_net_amount", "block_amount"]
    assert bundle["n_features"] == 3
    assert bundle["n_finite_rows"] > 0
    assert bundle["n_eligible_rows"] > 0


def test_pathfinder_and_ml_planner_behaviors_route_to_ml() -> None:
    from factor_lab.alpha.team.behavior import load_behavior

    pathfinder = load_behavior("pathfinder")
    assert "ml_branch" in pathfinder["contract"]["outputs"]
    assert "ML routing" in pathfinder["system_prompt"]
    ml_planner = load_behavior("ml-planner")
    assert "Expanded-axis rule" in ml_planner["system_prompt"]
    assert "ml_feature_catalog" in ml_planner["system_prompt"]


def test_run_ml_experiment_accepts_expanded_fields() -> None:
    from factor_lab.alpha.ml_branch import run_ml_experiment

    dataset = _dataset()
    plan = make_ml_plan(
        "expanded-axis ML runtime smoke",
        ["margin_rzye", "mf_net_amount", "block_amount"],
        target_horizon=5,
        n_splits=2,
        embargo_days=5,
    )
    result = run_ml_experiment(dataset, plan)
    assert result["schema"] == "factor_lab.alpha.ml_result.v1"
    assert result["model_card"]["features"] == ["margin_rzye", "mf_net_amount", "block_amount"]
    assert result["metrics"]["n_oos_rows"] > 0
