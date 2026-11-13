"""Regression tests for the research-review P0/P1 remediation batch."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from factor_lab.alpha.governance import (
    assert_holdout_unused,
    holdout_uses,
    policy_snapshot,
    record_holdout_use,
)
from factor_lab.alpha.power import analyze_power
from factor_lab.alpha.tradability import build_tradability_mask


def test_policy_snapshot_is_deterministic() -> None:
    first = policy_snapshot()
    second = policy_snapshot()
    assert first["policy_hash"] == second["policy_hash"]
    assert first["policy_version"]
    assert first["files"]


def test_holdout_guard_treats_historical_confirm_as_consumed(tmp_path, monkeypatch) -> None:
    import factor_lab.alpha.governance as governance

    monkeypatch.setattr(governance.config, "LAB_ROOT", tmp_path)
    confirm = tmp_path / "alpha" / "confirm"
    confirm.mkdir(parents=True)
    (confirm / "ALPHA-test.json").write_text(
        json.dumps(
            {
                "expression": "rank(close)",
                "dataset": {"start": "2025-11-01", "end": "2026-11-01", "data_hash": "x"},
                "evidence_status": "fail",
            }
        ),
        encoding="utf-8",
    )
    assert len(holdout_uses()) == 1
    with pytest.raises(ValueError, match="already been consumed"):
        assert_holdout_unused()
    with pytest.raises(ValueError, match="already consumed"):
        record_holdout_use(expression="rank(close)", data_hash="x")


def test_tradability_mask_blocks_limit_and_suspension() -> None:
    dates = pd.date_range("2024-01-01", periods=3)
    columns = ["600000", "000001"]
    close = pd.DataFrame(
        [[10.0, 10.0], [11.0, 10.0], [11.0, 10.0]], index=dates, columns=columns
    )
    high = close.copy()
    low = close.copy()
    volume = pd.DataFrame([[100.0, 100.0], [100.0, 0.0], [100.0, 100.0]], index=dates, columns=columns)
    ctx = type("Ctx", (), {"close": close, "fields": {"high": high, "low": low, "volume": volume}})()
    mask = build_tradability_mask(ctx)
    # The 600000 close reaches its 10% limit on day 2 (can't buy); 000001 is
    # suspended on day 2 (zero volume).
    assert not bool(mask.loc[dates[1], "600000"])
    assert not bool(mask.loc[dates[1], "000001"])


def test_power_exposes_rank1_threshold() -> None:
    result = analyze_power(n_periods=148, n_trials=276, n_eff=145, observed_t=2.3228)
    assert result["rank1_p"] == pytest.approx(0.10 / 145)
    assert result["rank1_z"] > 3.0
    assert 0.0 < result["pass_probability_under_observed_effect"] < 1.0


def test_rank_native_ml_transforms_roundtrip() -> None:
    from factor_lab.alpha.ml_branch import make_ml_plan, plan_from_dict

    plan = make_ml_plan(
        "rank-native test",
        ["returns", "turnover"],
        feature_transform="cs_rank",
        target_transform="cs_rank",
    )
    loaded = plan_from_dict(plan.to_dict())
    assert loaded.feature_transform == "cs_rank"
    assert loaded.target_transform == "cs_rank"


def test_alpha_cost_model_uses_actual_a_share_fees() -> None:
    from factor_lab.alpha.gates import alpha_gate_spec, load_gate_policy

    policy = load_gate_policy()
    spec = alpha_gate_spec()
    assert policy.commission_bps == 1.8
    assert policy.stamp_duty_bps == 5.0
    assert spec.cost.buy_fee_bps == pytest.approx(2.44)
    assert spec.cost.sell_fee_bps == pytest.approx(7.44)
    assert spec.cost.borrow_bps_per_year == pytest.approx(800.0)


def test_new_campaigns_are_alpha_campaign_v2(tmp_path) -> None:
    from factor_lab.alpha.campaign import CampaignController

    controller = CampaignController(tmp_path / "campaigns")
    record = controller.create("protocol v2 test", max_rounds=2)
    assert record["protocol_version"] == "alpha-campaign-v2"
    stored = controller.load(record["campaign_id"])
    assert stored["protocol_version"] == "alpha-campaign-v2"


def test_campaign_protocol_requires_gateway_and_ml_decision() -> None:
    import json
    from pathlib import Path

    roles = json.loads(Path("factor_lab/alpha/team/roles.json").read_text(encoding="utf-8"))
    protocol = roles["campaign_protocol"]
    assert "web-gateway" in protocol["required_external_per_campaign"]
    assert "ml-planner" in protocol["required_ml_decision_per_campaign"]
    assert "every new alpha-campaign-v2 campaign" in protocol["external_evidence_policy"]["default"].lower()
    assert "every new alpha-campaign-v2 campaign" in protocol["ml_planner_policy"]["default"].lower()


def test_ten_year_protocol_is_codified() -> None:
    from factor_lab.alpha.ten_year import load_protocol

    protocol = load_protocol()
    assert protocol["dataset_start"] == "2016-01-04"
    assert protocol["dataset_end"] == "2025-10-31"
    assert protocol["candidate_pool"]["top_n"] == 1000
    assert protocol["universe"]["top_n"] == 1000
    assert protocol["horizon"] == 20
    assert protocol["costs"]["buy_fee_bps"] == 2.44
    assert protocol["holdout"]["confirmation_allowed"] is False


def test_liquidity_pool_uses_gateway_query() -> None:
    import pandas as pd
    from factor_lab.alpha.ten_year import build_pool

    class FakeGateway:
        def liquidity_pool(self, *, start, end, top_n, min_amount=0.0):
            assert start == "2015-01-01"
            assert end == "2016-12-31"
            assert top_n == 1000
            return ["000001", "600000"]

    codes = build_pool(gateway=FakeGateway())
    assert codes == ["000001", "600000"]


def test_singlefold_protocol_is_codified() -> None:
    from factor_lab.alpha.ml_branch import (
        load_singlefold_protocol,
        make_ml_plan,
        validate_singlefold_plan,
    )

    protocol = load_singlefold_protocol()
    assert protocol["requirements"]["n_splits"] == 1
    assert protocol["requirements"]["distill_mode"] == "model_card"
    assert protocol["requirements"]["fidelity_policy"] == "report_only"
    plan = make_ml_plan(
        "singlefold test",
        ["returns", "turnover"],
        n_splits=1,
        feature_transform="cs_rank",
        target_transform="cs_rank",
        distill_mode="model_card",
        fidelity_policy="report_only",
    )
    assert validate_singlefold_plan(plan)["ok"] is True
    wrong = make_ml_plan("wrong split", ["returns"], n_splits=4)
    assert validate_singlefold_plan(wrong)["ok"] is False


def test_agent_team_change_control_codified() -> None:
    import json
    from pathlib import Path

    agents = Path("AGENTS.md").read_text(encoding="utf-8")
    contract = Path("factor_lab/alpha/ENGINEERING_CONTRACT.md").read_text(encoding="utf-8")
    assert "Agent-team change control" in agents
    assert "Conversation-only protocol changes are not binding" in agents
    assert "Agent-team change control" in contract
    roles = json.loads(Path("factor_lab/alpha/team/roles.json").read_text(encoding="utf-8"))
    assert "change_control" in roles["campaign_protocol"]
    assert Path("factor_lab/alpha/team/CHANGE_CONTROL.md").exists()
