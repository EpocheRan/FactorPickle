"""End-to-end system dry run.

Checks the current scaffold without launching a new research trial:
- no hardcoded idea catalogs
- role contracts load
- research context builds
- every experiment gets a canonical evaluation
- discovery mode and ML branch are wired
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from factor_lab.core import config
from factor_lab.core.contracts import ROLE_CONTRACTS
from factor_lab.core.store import read_json
from factor_lab.evaluation.evaluation import finalize
from factor_lab.research.research_context import build_context
from factor_lab.research.web_gateway import resolve_mode

HARDCODED = ("ideas.py", "cycle_ideas.py", "batch.py", "examples.py")


def run_dry_run() -> dict[str, Any]:
    lab = config.LAB_ROOT
    missing = [name for name in HARDCODED if (config.PROJECT_ROOT / "factor_lab" / name).exists()]
    context = build_context()
    evaluations = []
    for root in sorted(config.EXPERIMENTS_DIR.iterdir()) if config.EXPERIMENTS_DIR.exists() else []:
        if root.is_dir() and (root / "manifest.json").exists():
            evaluations.append(finalize(root))
    ml_dir = lab / "ml"
    ml_results = len(list(ml_dir.rglob("*.json"))) if ml_dir.exists() else 0
    code_sizes = [int((item.get("data") or {}).get("codes") or 0) for item in context.get("experiments", [])]
    code_ready = bool(code_sizes) and min(code_sizes) >= 200
    axis_violations = []
    for directive in sorted(lab.glob("campaigns/*/round-*/round_directive.json")):
        raw = read_json(directive) or {}
        axis = str(((raw.get("round_scope") or {}).get("data_axis")) or "").lower()
        if "ohlcv_1d only" in axis or "no financial/moneyflow/margin" in axis or "price/volume only" in axis:
            axis_violations.append(str(directive))
    blockers = []
    if missing:
        blockers.append("hardcoded catalog still present")
    if not code_ready:
        blockers.append("no experiment uses 200 codes")
    if any(item.get("status") in {"draft", "running", "failed"} for item in evaluations):
        blockers.append("evaluation has no canonical outcome")
    if axis_violations:
        blockers.append("round directive hardcodes a data axis")
    return {
        "schema_version": "factor_lab.dry_run.v1",
        "ok": not missing and len(evaluations) > 0 and code_ready and not blockers,
        "blockers": blockers,
        "hardcoded_catalogs_present": missing,
        "data_axis_violations": axis_violations,
        "role_contracts": sorted(ROLE_CONTRACTS),
        "experiments": len(evaluations),
        "evaluations": [
            {"exp_id": item["exp_id"], "status": item["status"], "reason": item["reason"]}
            for item in evaluations
        ],
        "context_path": str(lab / "research_context.json"),
        "context_experiments": len(context.get("experiments") or []),
        "code_sizes": code_sizes,
        "two_hundred_code_ready": code_ready,
        "web_mode": resolve_mode("exploit"),
        "ml_results": ml_results,
        "ml_branch_wired": (lab / "ml").exists(),
    }
