from __future__ import annotations

import json
from pathlib import Path

from factor_lab.audit import audit_experiment
from factor_lab.ledger import Ledger
from factor_lab.policy import default_policy
from factor_lab.schemas import Hypothesis, OutcomeSpec, trial_id
from factor_lab.store import write_json_atomic


def _fixture(root: Path, ledger: Ledger, *, run_id: str = "RUN-TEST", ledger_run_id: str | None = None, corrupt_ic: bool = True):
    policy = default_policy()
    hyp = Hypothesis(
        hypothesis_id="H-AUDIT-001",
        factor_id="liquidity.amount_bias_21_252",
        title="audit fixture",
        mechanism="fixture",
        expected_sign=1,
        outcomes=[OutcomeSpec(universe="pool:2022-01-04:200", horizon=5)],
    )
    outcome = hyp.outcomes[0]
    trial = trial_id(hyp.hypothesis_id, outcome)
    ledger.register_hypothesis(hyp)
    ledger.start_run(
        run_id=ledger_run_id or run_id,
        trial_id=trial,
        hypothesis_id=hyp.hypothesis_id,
        factor_id=hyp.factor_id,
        outcome=outcome.to_dict(),
    )
    root.mkdir(parents=True, exist_ok=True)
    write_json_atomic(root / "hypothesis.json", hyp.to_dict())
    write_json_atomic(
        root / "manifest.json",
        {
            "exp_id": root.name,
            "run_id": run_id,
            "factor_id": hyp.factor_id,
            "hypothesis_id": hyp.hypothesis_id,
            "trial_id": trial,
            "outcome": outcome.to_dict(),
            "params": {"start": "2019-01-01", "end": "2026-11-01", "profile": "quick"},
            "policy": {"version": policy["version"], "sha256": policy["sha256"]},
            "status": "success",
            "duration_sec": 1.0,
        },
    )
    values = [0.01] * 100
    mean = 0.02 if corrupt_ic else 0.01
    report = {
        "factor_id": hyp.factor_id,
        "horizons": [5],
        "start": "2019-01-29",
        "end": "2026-10-30",
        "n_symbols": 50,
        "n_dates": 100,
        "coverage": 0.9,
        "ic": {"5": {"index": list(range(100)), "values": values}},
        "ic_stats": [{"horizon": 5, "mean": mean, "n": 100}],
        "period_returns_gross": {
            "index": ["p1", "p2"],
            "columns": ["Q1", "Q5"],
            "data": [[0.0, 0.01], [0.0, 0.01]],
        },
        "spread_gross": {"mean": 0.01},
        "spread_net": {"mean": 0.005},
        "cost_mean": 0.005,
        "quantile_returns_gross": {"Q1": 0.0, "Q2": 0.0025, "Q3": 0.005, "Q4": 0.0075, "Q5": 0.01},
        "monotonicity": 1.0,
        "turnover": 0.2,
        "capacity": {"multiple": 4.0},
        "diagnostics": [],
        "rating": {"grade": "C", "score": 60.0},
        "warnings": [],
    }
    write_json_atomic(
        root / "report.json",
        {
            "meta": {"run_id": run_id},
            "factor": hyp.factor_id,
            "report": report,
            "rating": {"grade": "C", "score": 60.0},
            "html": str(root / "report.html"),
        },
    )
    (root / "report.html").write_text(
        f'<div><span class="tag">RUN</span>{run_id}</div>', encoding="utf-8"
    )
    return policy


def test_audit_detects_ic_mismatch(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "ledger.jsonl")
    root = tmp_path / "E-TEST"
    policy = _fixture(root, ledger, corrupt_ic=True)
    audit = audit_experiment(root, policy=policy, ledger=ledger)
    by_id = {item["check_id"]: item for item in audit["checks"]}
    assert by_id["ic_recompute"]["status"] == "fail"
    assert audit["verdict"] == "fail"


def test_audit_detects_orphan_run(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "ledger.jsonl")
    root = tmp_path / "E-ORPHAN"
    policy = _fixture(root, ledger, run_id="RUN-ORPHAN", ledger_run_id="RUN-OK", corrupt_ic=False)
    audit = audit_experiment(root, policy=policy, ledger=ledger)
    by_id = {item["check_id"]: item for item in audit["checks"]}
    assert by_id["orphan_run"]["status"] == "fail"
