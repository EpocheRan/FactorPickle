"""Alpha gate policy: tri-state evidence and capacity-as-warning."""

from __future__ import annotations

import pytest

from factor_lab.alpha.gates import default_policy_path, load_gate_policy
from factor_lab.alpha.pipeline import VERDICT_TO_ATTEMPT, card_status_for_verdict
from factor_lab.alpha.research_runtime import evidence_status, explain_result, policy_warnings
from factor_lab.core import config

PASS_METRICS = {
    "net_spread": 0.002,
    "ic_mean": 0.02,
    "turnover": 0.2,
    "capacity_multiple": 40.0,
    "n_dates": 50,
    "coverage": 0.9,
}


def test_gate_policy_is_repo_source_of_truth() -> None:
    path = default_policy_path()
    assert path.exists()
    assert not str(path.resolve()).startswith(str(config.LAB_ROOT.resolve()))
    policy = load_gate_policy()
    assert policy.min_dates_insufficient == 30
    assert policy.min_dates_warning == 60
    assert policy.to_dict()["schema"] == "factor_lab.alpha.gates.v2"


def test_null_metrics_are_insufficient_evidence_never_pass_or_fail() -> None:
    nan = float("nan")
    cases = [
        {},
        {"net_spread": nan, "ic_mean": 0.02, "turnover": 0.2, "n_dates": 50},
        {"net_spread": 0.002, "ic_mean": nan, "turnover": 0.2, "n_dates": 50},
        {"net_spread": 0.002, "ic_mean": 0.02, "turnover": nan, "n_dates": 50},
        {**PASS_METRICS, "n_dates": 5},
        {**PASS_METRICS, "n_dates": 0},
        {**PASS_METRICS, "n_dates": 29},
    ]
    for metrics in cases:
        assert evidence_status(metrics) == "insufficient_evidence"
        explained = explain_result({"metrics": metrics})
        assert explained["verdict"] == "insufficient_evidence"
        assert explained["next_action"] == "collect_more_evidence"
        assert explained["reasons"][0].startswith("insufficient evidence")


def test_two_level_sample_thresholds_are_independent() -> None:
    policy = load_gate_policy()
    insufficient = policy.min_dates_insufficient
    warning = policy.min_dates_warning
    # Between the two levels: verdict is allowed, with a warning only.
    between = {**PASS_METRICS, "n_dates": insufficient + 1}
    assert evidence_status(between) == "pass"
    explained = explain_result({"metrics": between})
    assert explained["verdict"] == "pass"
    assert any("below preferred" in item for item in explained["warnings"])
    # At/above the warning level: no sample-size warning.
    strong = {**PASS_METRICS, "n_dates": warning}
    assert not any("below preferred" in item for item in explain_result({"metrics": strong})["warnings"])
    # Each level can be changed independently through the policy object.
    between_warnings = policy_warnings({**PASS_METRICS, "n_dates": insufficient + 1})
    assert any("below preferred" in item for item in between_warnings)
    assert not any("below preferred" in item for item in policy_warnings(strong))
    custom = type(policy)(min_dates_insufficient=40, min_dates_warning=80)
    assert evidence_status({**PASS_METRICS, "n_dates": 45}, custom) == "pass"
    assert evidence_status({**PASS_METRICS, "n_dates": 35}, custom) == "insufficient_evidence"
    assert any(
        "below preferred" in item
        for item in explain_result({"metrics": {**PASS_METRICS, "n_dates": 45}}, custom)["warnings"]
    )


def test_pass_and_fail_verdicts_are_unchanged() -> None:
    assert evidence_status(PASS_METRICS) == "pass"
    assert explain_result({"metrics": PASS_METRICS})["verdict"] == "pass"
    fail = {**PASS_METRICS, "net_spread": -0.001}
    assert evidence_status(fail) == "fail"
    explained = explain_result({"metrics": fail})
    assert explained["verdict"] == "fail"
    assert explained["next_action"] == "record_failure"


def test_capacity_is_warning_only() -> None:
    for capacity in (None, 0.0):
        metrics = {**PASS_METRICS, "capacity_multiple": capacity}
        explained = explain_result({"metrics": metrics})
        assert explained["verdict"] == "pass"  # capacity never vetoes
        assert any("capacity" in warning for warning in explained["warnings"])
        assert not any("capacity" in reason for reason in explained["reasons"])
    assert any("capacity" in caveat for caveat in explain_result({"metrics": PASS_METRICS})["caveats"])


def test_verdict_to_card_and_attempt_status() -> None:
    assert card_status_for_verdict("pass") == "research"
    assert card_status_for_verdict("fail") == "killed"
    assert card_status_for_verdict("insufficient_evidence") == "parked"
    assert card_status_for_verdict("anything-else") == "parked"
    assert VERDICT_TO_ATTEMPT["insufficient_evidence"] == "insufficient_evidence"

def test_confirm_marks_insufficient_evidence_as_parked(tmp_path, monkeypatch) -> None:
    """Holdout confirmation must not turn null metrics into pass or kill."""
    import json

    import numpy as np
    import pandas as pd

    from factor_lab.alpha import confirm as confirm_mod
    from factor_lab.alpha.compiler import AlphaContext
    from factor_lab.alpha.data import AlphaDataset, HoldoutWindow

    monkeypatch.setattr(confirm_mod.config, "LAB_ROOT", tmp_path)
    rng = np.random.default_rng(3)
    dates = pd.bdate_range("2023-01-02", periods=120)
    columns = [f"{600000 + i:06d}" for i in range(5)]
    rets = pd.DataFrame(rng.normal(0.0005, 0.018, (len(dates), 5)), index=dates, columns=columns)
    close = 10.0 * (1 + rets).cumprod()
    context = AlphaContext.from_fields({"close": close, "open": close * 1.001}, close=close)
    dataset = AlphaDataset(
        context=context,
        codes=columns,
        start=str(dates.min().date()),
        end=str(dates.max().date()),
        warmup_days=10,
        data_manifest={"fields": context.field_names, "codes": 5},
    )
    holdout = HoldoutWindow(
        dev_start="2010-01-01",
        dev_end="2019-12-31",
        holdout_start=str(dates.min().date()),
        holdout_end=str(dates.max().date()),
        locked=True,
    )
    search_path = tmp_path / "search.json"
    search_path.write_text(
        json.dumps({"top": [{"expression": "rank(close)", "metrics": {}, "score": 0.0}]}),
        encoding="utf-8",
    )
    # without a passed freeze the one-shot holdout must be refused
    with pytest.raises(ValueError, match="passed freeze"):
        confirm_mod.confirm_search(
            str(search_path), dataset, horizon=5, cost_bps=20.0, holdout=holdout
        )

    # a passed freeze lets the confirmation run; the holdout metrics are still
    # insufficient -> parked, not pass/fail.
    from factor_lab.alpha.freeze import freeze_path
    from factor_lab.alpha.memory import canonical_expression

    expression = "rank(close)"
    decision = {
        "schema": "factor_lab.alpha.freeze_decision.v1",
        "expression": expression,
        "canonical_expression": canonical_expression(expression),
        "passed": True,
        "created_at": "2026-11-12T00:00:00+00:00",
        "family_id": "test-family",
    }
    freeze_path(expression).parent.mkdir(parents=True, exist_ok=True)
    freeze_path(expression).write_text(json.dumps(decision), encoding="utf-8")

    result = confirm_mod.confirm_search(
        str(search_path), dataset, horizon=5, cost_bps=20.0, holdout=holdout
    )
    assert result["evidence_status"] == "insufficient_evidence"
    assert result["passed"] is False
    assert result["red_team"]["passed"] is False
    assert result["path"].startswith(str(tmp_path))



def test_long_only_primary_keeps_long_short_as_diagnostic() -> None:
    result = {
        "metrics": {
            "net_spread": -0.001,
            "net_t": -1.0,
            "ic_mean": -0.01,
            "turnover": 0.2,
            "n_dates": 107,
            "coverage": 0.955,
        }
    }
    explained = explain_result(
        result,
        research_evidence={"primary": "long_only", "long_short_role": "diagnostic"},
        long_only_status="pass",
        long_only_reasons=[],
    )
    assert explained["verdict"] == "pass"
    assert explained["primary_evidence"] == "long_only"
    assert explained["long_short_verdict"] == "fail"
    assert any("diagnostic" in caveat for caveat in explained["caveats"])
