"""Run one pre-registered hypothesis through FactorPickle's report generator."""

from __future__ import annotations

import shutil
import time
import uuid
from pathlib import Path
from typing import Any

from .research_adapter import RESEARCH

from . import config
from .audit import audit_experiment
from .brief import build_brief, write_decision
from .control import assert_running, ensure_control
from .ledger import Ledger
from .policy import init_policy, load_policy
from .schemas import Hypothesis, artifact_paths, experiment_dir, reserve_experiment_id, trial_id
from .serialization import report_to_dict
from .staging import ensure_staged
from .store import write_json_atomic


def run_experiment(
    hypothesis: Hypothesis,
    *,
    profile: str = "quick",
    robustness: bool = False,
    use_cache: bool = True,
    with_peers: bool = True,
    auto_audit: bool = True,
    panel: Any | None = None,
) -> dict[str, Any]:
    config.ensure_dirs()
    assert_running()
    policy = load_policy()
    ledger = Ledger()
    exp_id = reserve_experiment_id()
    root = experiment_dir(exp_id)

    outcome = hypothesis.outcomes[0]
    trial = trial_id(hypothesis.hypothesis_id, outcome)
    staged = ensure_staged(hypothesis.factor_id)
    run_id = f"LAB-{exp_id}-{uuid.uuid4().hex[:8]}"

    hypothesis_path = root / "hypothesis.json"
    write_json_atomic(hypothesis_path, hypothesis.to_dict())
    write_json_atomic(
        root / "factor_spec.json",
        {
            "schema_version": config.SCHEMA_VERSION,
            "factor_id": hypothesis.factor_id,
            "staged": staged is not None,
            "staging": staged.__dict__ if staged else None,
            "outcome": outcome.to_dict(),
            "expected_sign": hypothesis.expected_sign,
            "source": hypothesis.source,
            "bet": hypothesis.bet,
            "family_id": hypothesis.family_id,
            "development_window": hypothesis.development_window,
        },
    )

    hypothesis_event = ledger.register_hypothesis(hypothesis)
    ledger.start_run(
        run_id=run_id,
        trial_id=trial,
        hypothesis_id=hypothesis.hypothesis_id,
        factor_id=hypothesis.factor_id,
        outcome=outcome.to_dict(),
        family_id=hypothesis.family_id,
        parent_event_id=hypothesis_event.get("event_id"),
    )

    started = time.time()
    manifest: dict[str, Any] = {
        "schema_version": config.SCHEMA_VERSION,
        "exp_id": exp_id,
        "run_id": run_id,
        "factor_id": hypothesis.factor_id,
        "hypothesis_id": hypothesis.hypothesis_id,
        "trial_id": trial,
        "family_id": hypothesis.family_id,
        "development_window": hypothesis.development_window,
        "outcome": outcome.to_dict(),
        "params": {
            "start": hypothesis.start,
            "end": hypothesis.end,
            "profile": profile,
            "robustness": robustness,
            "universe": outcome.universe,
            "horizon": outcome.horizon,
            "groups": outcome.groups,
            "direction": outcome.direction,
            "cost_bps": outcome.cost_bps,
        },
        "git_commit": config.git_commit(),
        "policy": {"version": policy["version"], "sha256": policy["sha256"]},
        "evaluator": RESEARCH.source,
        "data_path": "runner_panel" if panel is not None else "research_loader",
        "started_at": config.now_iso(),
        "status": "running",
    }
    write_json_atomic(root / "manifest.json", manifest)

    try:
        result = RESEARCH.generate(
            hypothesis.factor_id,
            start=hypothesis.start,
            end=hypothesis.end,
            universe=outcome.universe,
            direction=outcome.direction,
            out_dir=root / "html",
            with_peers=with_peers,
            robustness=robustness,
            run_id=run_id,
            verify_repro=True,
            horizon=outcome.horizon,
            groups=outcome.groups,
            profile=profile,
            use_cache=use_cache,
            panel=panel,
        )
        report_html = result.html
        stable_html = root / "report.html"
        if report_html.exists():
            shutil.copy2(report_html, stable_html)
        else:
            raise FileNotFoundError(f"report html not found: {report_html}")

        report_payload = {
            "schema_version": config.REPORT_SCHEMA_VERSION,
            "exp_id": exp_id,
            "factor": hypothesis.factor_id,
            "meta": result.meta,
            "report": report_to_dict(result.report),
            "rating": result.rating,
            "html": str(stable_html),
        }
        write_json_atomic(root / "report.json", report_payload)

        manifest.update(
            {
                "status": "success",
                "finished_at": config.now_iso(),
                "duration_sec": round(time.time() - started, 3),
                "data_version": (result.meta or {}).get("data_version"),
                "report_meta": result.meta,
                "paths": artifact_paths(exp_id),
            }
        )
        write_json_atomic(root / "manifest.json", manifest)
        ledger.finish_run(
            run_id=run_id,
            status="success",
            report_path=str(root / "report.json"),
            duration_sec=manifest["duration_sec"],
        )
    except Exception as exc:  # noqa: BLE001
        manifest.update(
            {
                "status": "failed",
                "finished_at": config.now_iso(),
                "duration_sec": round(time.time() - started, 3),
                "error": f"{type(exc).__name__}: {exc}",
            }
        )
        write_json_atomic(root / "manifest.json", manifest)
        ledger.finish_run(
            run_id=run_id,
            status="failed",
            duration_sec=manifest["duration_sec"],
            error=manifest["error"],
        )
        raise

    result_payload: dict[str, Any] = {
        "exp_id": exp_id,
        "run_id": run_id,
        "trial_id": trial,
        "family_id": hypothesis.family_id,
        "experiment_dir": str(root),
        "manifest": str(root / "manifest.json"),
        "report": str(root / "report.json"),
        "report_html": str(root / "report.html"),
        "rating": result.rating,
        "evaluator": RESEARCH.source,
    }
    if auto_audit:
        audit = audit_experiment(root)
        brief = build_brief(root)
        decision = write_decision(root, brief, audit)
        result_payload.update(
            {
                "audit": str(root / "audit.json"),
                "decision_brief": str(root / "decision_brief.json"),
                "decision": str(root / "decision.json"),
                "audit_verdict": audit.get("verdict"),
                "recommend": brief.get("recommend"),
                "decision_value": decision.get("decision"),
            }
        )
    ledger.write_summary()
    return result_payload


def run_pilot() -> dict[str, Any]:
    from .examples import pilot_coverage, pilot_hypothesis

    config.ensure_dirs()
    init_policy()
    ensure_control()
    if not config.DATA_COVERAGE_PATH.exists():
        write_json_atomic(config.DATA_COVERAGE_PATH, pilot_coverage())
    hypothesis = pilot_hypothesis()
    write_json_atomic(config.HYPOTHESES_DIR / f"{hypothesis.hypothesis_id}.json", hypothesis.to_dict())
    return run_experiment(hypothesis)
