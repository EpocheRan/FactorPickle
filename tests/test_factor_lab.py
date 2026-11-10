from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from factor_lab import control, dashboard
from factor_lab.ledger import Ledger
from factor_lab.policy import default_policy, verify_policy
from factor_lab.schemas import Hypothesis, OutcomeSpec, trial_id
from factor_lab.stats import classify_rho, two_sided_p
from factor_lab.store import jsonable, read_json, write_json_atomic


def _hypothesis() -> Hypothesis:
    return Hypothesis(
        hypothesis_id="H-TEST-001",
        factor_id="liquidity.amount_bias_21_252",
        title="test",
        mechanism="test mechanism",
        expected_sign=1,
        outcomes=[OutcomeSpec(universe="pool:2022-01-04:200", horizon=5)],
    )


def test_jsonable_and_atomic_json(tmp_path: Path) -> None:
    assert jsonable(float("nan")) is None
    assert jsonable(float("inf")) is None
    frame = pd.DataFrame({"a": [1.0, float("nan")]})
    payload = {"frame": frame, "x": float("nan")}
    target = write_json_atomic(tmp_path / "x.json", payload)
    loaded = read_json(target)
    assert loaded["x"] is None
    assert loaded["frame"]["data"][1][0] is None


def test_policy_hash_roundtrip() -> None:
    policy = default_policy()
    ok, expected = verify_policy(policy)
    assert ok
    tampered = dict(policy)
    tampered["sha256"] = "bad"
    assert not verify_policy(tampered)[0]
    assert expected == policy["sha256"]


def test_rho_ci_classification() -> None:
    assert classify_rho(0.91, 0.95) == "fail"
    assert classify_rho(-0.95, -0.91) == "fail"
    assert classify_rho(0.60, 0.69) == "pass"
    assert classify_rho(-0.69, -0.60) == "pass"
    assert classify_rho(0.60, 0.95) == "warn"
    assert classify_rho(-0.2, 0.4) == "pass"
    assert classify_rho(None, None) == "warn"


def test_two_sided_p() -> None:
    assert two_sided_p(0.0) == 1.0
    assert 0.0 < two_sided_p(2.0) < 0.05


def test_ledger_chain_and_orphan_check(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "ledger.jsonl")
    hyp = _hypothesis()
    outcome = hyp.outcomes[0]
    trial = trial_id(hyp.hypothesis_id, outcome)
    ledger.register_hypothesis(hyp)
    ledger.start_run(
        run_id="RUN-1",
        trial_id=trial,
        hypothesis_id=hyp.hypothesis_id,
        factor_id=hyp.factor_id,
        outcome=outcome.to_dict(),
    )
    ledger.finish_run(run_id="RUN-1", status="success")
    assert ledger.verify()["ok"]
    assert ledger.orphan_check(run_id="RUN-1", hypothesis_id=hyp.hypothesis_id, trial_id=trial)["ok"]
    assert ledger.summary()["distinct_trials"] == 1


def test_ledger_repair_covers_gap(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    ledger = Ledger(path)
    ledger.append("one", {"v": 1})
    ledger.append("two", {"v": 2})
    lines = path.read_text(encoding="utf-8").splitlines()
    broken = json.loads(lines[1])
    broken["prev_hash"] = "BROKEN"
    lines[1] = json.dumps(broken, ensure_ascii=False, separators=(",", ":"))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert not ledger.verify()["ok"]
    ledger.repair(gap_start=2, gap_end=2, reason="test crash", approved_by="human")
    verified = ledger.verify()
    assert verified["ok"]
    assert verified["repairs"]


def test_control_pause_blocks_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(control, "CONTROL_PATH", tmp_path / "control.json")
    control.ensure_control()
    control.assert_running()
    control.set_state("paused", reason="test")
    with pytest.raises(RuntimeError):
        control.assert_running()
    control.set_state("running")
    control.assert_running()


def test_staging_injects_in_process() -> None:
    from factor_lab.staging import ensure_staged, unstage
    from factors.ops import price_factors

    try:
        staged = ensure_staged("liquidity.amount_bias_21_252")
        assert staged is not None
        fn, window, unit = price_factors.lookup("liquidity.amount_bias_21_252")
        assert window == 252
        assert unit == "ratio"
    finally:
        unstage("liquidity.amount_bias_21_252")


def test_idea_specs_are_unique_and_well_formed() -> None:
    from factor_lab.ideas import IDEA_SPECS

    ids = [spec.factor_id for spec in IDEA_SPECS]
    assert len(ids) == len(set(ids))
    assert len(ids) >= 20
    for spec in IDEA_SPECS:
        assert spec.direction in (-1, 1)
        assert spec.window > 0
        hypothesis = spec.hypothesis()
        assert hypothesis.outcome_policy == "single"
        assert len(hypothesis.outcomes) == 1


def test_dashboard_renders_embedded_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data = {
        "meta": {
            "generated_at": "2026-11-08T00:00:00+00:00",
            "git_commit": "test",
            "policy_version": "1.0.0",
            "policy_hash": "x",
            "ledger_head": "GENESIS",
            "experiment_count": 1,
        },
        "health": {"level": "yellow", "reasons": ["G3 tradeability is not fully proven"]},
        "kpis": [{"label": "Weekly hypotheses", "value": 1, "sub": "target", "tone": "warn"}],
        "funnel": [{"gate": "G0", "count": 1, "note": "x"}],
        "experiments": [
            {
                "id": "E-1",
                "factor": "liquidity.amount_bias_21_252",
                "bet": "liquidity-second-order",
                "gate": "G3 boundary",
                "ic": -0.02,
                "net": -0.01,
                "turnover": 0.15,
                "max_rho": None,
                "n_trials": 1,
                "q_value": None,
                "verdict": "warn",
                "g3": "warn",
                "recommend": "park",
                "decision": "park",
                "detail": {"audit_checks": [], "brief": {}, "paths": {}},
            }
        ],
        "attention": [],
        "bets": [],
        "audit_findings": [],
        "ledger_points": [],
        "coverage": [],
        "storage": {"output_bytes": 1, "panel_cache": 1, "top": []},
        "control": {"state": "running"},
        "ledger": {"distinct_trials": 1},
    }
    monkeypatch.setattr(dashboard.config, "DASHBOARD_DATA_PATH", tmp_path / "data.json")
    out = dashboard.render_dashboard(data, out_path=tmp_path / "dashboard.html")
    html = out.read_text(encoding="utf-8")
    assert "Factor Lab" in html
    assert "liquidity.amount_bias_21_252" in html
    assert "研究漏斗" in html
    assert "实验记录" in html
    assert 'id="funnel"' in html
    assert 'id="tbody"' in html
    assert 'id="pager"' in html
    assert 'data-page' in html
