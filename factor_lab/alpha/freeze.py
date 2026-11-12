"""Deterministic freeze decision (P1).

A freeze decision is the only transition that allows an Edge Card to be sent
to the one-shot holdout confirmation.  It requires:

- tri-state evidence == pass;
- family FDR pass (pre-registered q, N_eff with sensitivity);
- coverage >= 0.90 and positive post-cost net spread;
- minimum sample size from the gate policy.

Holdout confirmation itself is not run here.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from factor_lab.alpha.fdr import family_fdr, load_family_policy, load_ledger, program_fdr
from factor_lab.alpha.gates import load_gate_policy
from factor_lab.alpha.memory import canonical_expression
from factor_lab.alpha.research_runtime import evidence_status
from factor_lab.core import config
from factor_lab.core.store import write_json_atomic


FREEZE_SCHEMA = "factor_lab.alpha.freeze_decision.v1"


def freeze_dir() -> Path:
    path = config.LAB_ROOT / "alpha" / "freeze"
    path.mkdir(parents=True, exist_ok=True)
    return path


def freeze_path(expression: str) -> Path:
    canonical = canonical_expression(expression)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    return freeze_dir() / f"{digest}.json"


def _latest_trial(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not rows:
        return None
    return max(
        rows,
        key=lambda row: (int(row.get("n_dates") or 0), str(row.get("created_at") or "")),
    )


def freeze_expression(
    expression: str,
    *,
    family_id: str | None = None,
    campaign_id: str | None = None,
    q: float | None = None,
    write: bool = True,
) -> dict[str, Any]:
    canonical = canonical_expression(expression)
    ledger = load_ledger()
    matches = [row for row in ledger if row.get("canonical_expression") == canonical and row.get("p_net") is not None]
    trial = _latest_trial(matches)
    if trial is None:
        raise ValueError(f"freeze requires a recorded trial: {expression}")
    effective_family = family_id or str(trial.get("family_id") or "")
    if not effective_family:
        raise ValueError("freeze requires a family id")
    fdr = program_fdr(expression, q=q, ledger=ledger, include_legacy=False)
    family_fdr_result = family_fdr(effective_family, expression, q=q, ledger=ledger)
    metrics = trial.get("metrics") or {}
    status = evidence_status(metrics)
    min_dates = int(load_gate_policy().min_dates_insufficient)
    checks = {
        "evidence_status_pass": status == "pass",
        "program_fdr_pass": bool(fdr.get("passed")),
        "coverage_ge_0_90": bool((metrics.get("coverage") or 0.0) >= 0.90),
        "net_spread_positive": bool((metrics.get("net_spread") or 0.0) > 0.0),
        "min_dates": bool(int(metrics.get("n_dates") or 0) >= min_dates),
    }
    blocked = [name for name, ok in checks.items() if not ok]
    payload = {
        "schema": FREEZE_SCHEMA,
        "expression": expression,
        "canonical_expression": canonical,
        "family_id": effective_family,
        "campaign_id": campaign_id or trial.get("campaign_id"),
        "trial_id": trial.get("trial_id"),
        "evidence_status": status,
        "metrics": {
            key: metrics.get(key)
            for key in ("n_dates", "coverage", "ic_mean", "ic_t", "net_spread", "net_t", "turnover", "capacity_multiple")
        },
        "program_fdr": {
            key: fdr.get(key)
            for key in ("family_id", "denominator", "n_unique", "n_eff", "n_eff_method", "n_eff_threshold", "rank", "p_net", "p_net_two_sided", "bh_q", "bh_threshold", "bh_threshold_t", "raw_bh_threshold_t", "passed", "sensitivity", "ledger_hash")
        },
        "family_fdr": {
            key: family_fdr_result.get(key)
            for key in ("n_unique", "n_eff", "rank", "p_net", "bh_q", "bh_threshold", "bh_threshold_t", "passed")
        },
        "checks": checks,
        "blocked_reasons": blocked,
        "passed": all(checks.values()),
        "created_at": config.now_iso(),
    }
    if write:
        target = freeze_path(expression)
        payload["path"] = str(target)
        write_json_atomic(target, payload)
    return payload


def latest_passed_freeze(expression: str) -> dict[str, Any] | None:
    canonical = canonical_expression(expression)
    directory = freeze_dir()
    candidates: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if payload.get("canonical_expression") != canonical:
            continue
        if not payload.get("passed"):
            continue
        payload["path"] = str(path)
        candidates.append(payload)
    if not candidates:
        return None
    return max(candidates, key=lambda item: str(item.get("created_at") or ""))


def freeze_list() -> list[dict[str, Any]]:
    rows = []
    for path in sorted(freeze_dir().glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        rows.append(
            {
                "expression": payload.get("expression"),
                "family_id": payload.get("family_id"),
                "passed": payload.get("passed"),
                "blocked_reasons": payload.get("blocked_reasons"),
                "bh_q": (payload.get("family_fdr") or {}).get("bh_q"),
                "path": str(path),
            }
        )
    return rows
