"""Batch mining runner for the P1 idea set."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from . import config
from .audit import audit_experiment
from .brief import build_brief, write_decision
from .control import ensure_control
from .dashboard import render_dashboard
from .ideas import IDEA_SPECS
from .orthogonality import compute_batch_orthogonality
from .policy import init_policy
from .run import run_experiment
from .schemas import experiment_dir
from .store import read_json, write_json_atomic


def _existing_factors() -> set[str]:
    factors: set[str] = set()
    if not config.EXPERIMENTS_DIR.exists():
        return factors
    for root in config.EXPERIMENTS_DIR.iterdir():
        manifest = read_json(root / "manifest.json") or {}
        if manifest.get("factor_id"):
            factors.add(str(manifest["factor_id"]))
    return factors


def _write_pilot_coverage() -> None:
    from .examples import pilot_coverage

    if not config.DATA_COVERAGE_PATH.exists():
        write_json_atomic(config.DATA_COVERAGE_PATH, pilot_coverage())


def run_batch(
    *,
    limit: int = 23,
    profile: str = "quick",
    orthogonality: bool = True,
    include_existing: bool = False,
) -> dict[str, Any]:
    config.ensure_dirs()
    init_policy()
    ensure_control()
    _write_pilot_coverage()
    existing = _existing_factors()
    specs = [
        spec for spec in IDEA_SPECS
        if include_existing or spec.factor_id not in existing
    ]
    if limit > 0:
        specs = specs[:limit]

    successes: list[Path] = []
    failures: list[dict[str, str]] = []
    started = time.time()
    for index, spec in enumerate(specs, start=1):
        hypothesis = spec.hypothesis()
        write_json_atomic(
            config.HYPOTHESES_DIR / f"{hypothesis.hypothesis_id}.json",
            hypothesis.to_dict(),
        )
        print(f"[{index}/{len(specs)}] {spec.factor_id}", flush=True)
        try:
            result = run_experiment(hypothesis, profile=profile, auto_audit=True)
            successes.append(Path(result["experiment_dir"]))
            print(f"    ok {result['exp_id']} -> {result.get('decision_value')}", flush=True)
        except Exception as exc:  # noqa: BLE001
            failures.append({"factor_id": spec.factor_id, "error": f"{type(exc).__name__}: {exc}"})
            print(f"    FAIL {type(exc).__name__}: {exc}", flush=True)

    orthogonality_results: list[dict[str, Any]] = []
    if orthogonality and successes:
        print(f"[orthogonality] {len(successes)} experiments", flush=True)
        try:
            orthogonality_results = compute_batch_orthogonality(successes)
        except Exception as exc:  # noqa: BLE001
            failures.append({"factor_id": "__orthogonality__", "error": f"{type(exc).__name__}: {exc}"})
            print(f"    ORTHO FAIL {type(exc).__name__}: {exc}", flush=True)

    # Re-audit after orthogonality so G0.5 and q are in the final artifacts.
    for root in successes:
        try:
            audit = audit_experiment(root)
            brief = build_brief(root)
            write_decision(root, brief, audit)
        except Exception as exc:  # noqa: BLE001
            failures.append({"factor_id": root.name, "error": f"post-audit {type(exc).__name__}: {exc}"})

    dashboard = render_dashboard()
    elapsed = round(time.time() - started, 1)
    summary = {
        "requested": len(specs),
        "successes": len(successes),
        "failures": failures,
        "orthogonality": len(orthogonality_results),
        "dashboard": str(dashboard),
        "elapsed_sec": elapsed,
        "experiments": [root.name for root in successes],
    }
    write_json_atomic(config.LAB_ROOT / "last_batch.json", summary)
    return summary
