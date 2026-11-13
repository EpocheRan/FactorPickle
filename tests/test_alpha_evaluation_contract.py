"""Evaluation contract and frozen forward-holdout policy."""

from __future__ import annotations

import pytest

from factor_lab.alpha.evaluation_contract import (
    EVALUATION_CONTRACT_SCHEMA,
    HoldoutLockedError,
    assert_holdout_allowed,
    build_evaluation_contract,
    load_holdout_v2,
    validate_evaluation_contract,
)
from factor_lab.alpha.prereg import PRE_REG_SCHEMA, validate_pre_registration


def test_forward_holdout_is_frozen() -> None:
    holdout = load_holdout_v2()
    assert holdout["holdout_start"] == "2026-11-02"
    assert holdout["confirmation_allowed"] is False
    with pytest.raises(HoldoutLockedError):
        assert_holdout_allowed("2017-01-03", "2026-11-06")


def test_evaluation_contract_requires_all_conditions() -> None:
    contract = build_evaluation_contract(
        evaluation_start="2017-01-03",
        evaluation_end="2025-10-31",
        universe="pit_liquidity_1000_size_screen",
        size_screen="exclude_smallest_30pct",
        horizon=20,
        cost={"buy_fee_bps": 2.44, "sell_fee_bps": 7.44, "borrow_bps_per_year": 0.0},
        n_eff=62,
        n_trials=440,
        hypothesis_count=5,
        pre_registration_hash="abc",
    )
    assert contract["schema"] == EVALUATION_CONTRACT_SCHEMA
    assert contract["evidence_kind"] == "development_evaluation"
    assert validate_evaluation_contract(contract)["ok"] is True
    broken = dict(contract)
    broken["horizon"] = None
    assert validate_evaluation_contract(broken)["ok"] is False


def test_pre_registration_requires_evaluation_contract_and_candidate_cap() -> None:
    base = {
        "schema": PRE_REG_SCHEMA,
        "campaign_id": "CAMP-TEST",
        "research_question": "q",
        "evaluation": {
            "start": "2017-01-03",
            "end": "2025-10-31",
            "universe": "pit_liquidity_1000_size_screen",
            "horizon": 20,
            "cost": {"buy": 2.44, "sell": 7.44},
        },
        "families": [
            {
                "family_id": "fam-max-lottery",
                "candidates": [
                    {
                        "candidate_id": "c1",
                        "expression": "neg(rank(div(ts_delta(margin_rzye, 5), float_mv)))",
                        "kill_criteria": ["net_t<2"],
                    }
                ],
            }
        ],
    }
    assert validate_pre_registration(base)["ok"] is True
    missing = dict(base)
    missing.pop("evaluation")
    assert validate_pre_registration(missing)["ok"] is False
    too_many = dict(base)
    too_many["families"] = [
        {
            "family_id": "fam-max-lottery",
            "candidates": [
                {
                    "candidate_id": f"c{i}",
                    "expression": "neg(rank(div(ts_delta(margin_rzye, 5), float_mv)))",
                    "kill_criteria": ["net_t<2"],
                }
                for i in range(11)
            ],
        }
    ]
    assert validate_pre_registration(too_many)["ok"] is False


def test_deployment_is_explicitly_blocked() -> None:
    from factor_lab.alpha.deployment_readiness import deployment_readiness

    payload = deployment_readiness()
    assert payload["schema"] == "factor_lab.alpha.deployment_readiness.v1"
    assert payload["ready"] is False
    assert "execution bridge" in " ".join(payload["blockers"])
