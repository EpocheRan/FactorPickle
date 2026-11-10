from __future__ import annotations

import json
from pathlib import Path

from factor_lab import evaluation, research_context
from factor_lab.contracts import validate
from factor_lab.store import write_json_atomic
from factor_lab.web_gateway import mode_requires_web, resolve_mode


def test_role_contract_rejects_incomplete_payload() -> None:
    assert not validate("pathfinder", {"action": "factor"})["ok"]
    assert validate("pathfinder", {"action": "factor", "reason": "because"})["ok"]


def _candidate(root: Path) -> None:
    write_json_atomic(root / "manifest.json", {
        "exp_id": root.name,
        "status": "success",
        "factor_id": "test.factor",
        "hypothesis_id": "H-test",
        "outcome": {"universe": "explicit:200", "horizon": 20},
    })
    write_json_atomic(root / "hypothesis.json", {
        "hypothesis_id": "H-test",
        "factor_id": "test.factor",
        "kill_criteria": ["turnover > 0.6"],
    })
    write_json_atomic(root / "data_manifest.json", {
        "data_path": "runner_panel",
        "factor_artifact": {"code_hash": "abc", "version": "v1"},
    })
    write_json_atomic(root / "report.json", {"rating": {"grade": "B", "score": 72.0}, "report": {}})
    write_json_atomic(root / "audit.json", {
        "verdict": "pass",
        "g3": "pass",
        "metrics": {"turnover": 0.3},
        "checks": [{"check_id": "orthogonality", "status": "pass"}],
    })


def test_evaluation_candidate_and_kill(tmp_path: Path) -> None:
    root = tmp_path / "E-1"
    root.mkdir()
    _candidate(root)
    assert evaluation.derive(root)["status"] == "candidate"

    audit = json.loads((root / "audit.json").read_text(encoding="utf-8"))
    audit["metrics"]["turnover"] = 0.7
    write_json_atomic(root / "audit.json", audit)
    assert evaluation.derive(root)["status"] == "parked"


def test_evaluation_needs_orthogonality(tmp_path: Path) -> None:
    root = tmp_path / "E-2"
    root.mkdir()
    _candidate(root)
    audit = json.loads((root / "audit.json").read_text(encoding="utf-8"))
    audit["checks"] = []
    write_json_atomic(root / "audit.json", audit)
    assert evaluation.derive(root)["status"] == "needs_fix"


def test_web_modes() -> None:
    assert not mode_requires_web("exploit")
    assert mode_requires_web("explore")
    assert resolve_mode("explore")["mode"] == "explore"


def test_research_context_build(tmp_path: Path, monkeypatch) -> None:
    experiments = tmp_path / "experiments"
    root = experiments / "E-1"
    root.mkdir(parents=True)
    _candidate(root)
    monkeypatch.setattr(research_context.config, "EXPERIMENTS_DIR", experiments)
    monkeypatch.setattr(research_context.config, "LAB_ROOT", tmp_path)
    monkeypatch.setattr(research_context.config, "DATA_COVERAGE_PATH", tmp_path / "coverage.json")
    monkeypatch.setattr(research_context, "CONTEXT_PATH", tmp_path / "research_context.json")
    write_json_atomic(tmp_path / "coverage.json", [])
    data = research_context.build_context()
    assert data["schema_version"] == "factor_lab.research_context.v1"
    assert data["experiments"][0]["factor_id"] == "test.factor"
