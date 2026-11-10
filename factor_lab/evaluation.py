"""Canonical evaluation state for one experiment.

Only this module writes ``evaluation.json``.  Stage agents may describe their
own outputs; they may not invent a final evaluation status.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import config
from .store import read_json, write_json_atomic

STATUSES = ("draft", "running", "needs_fix", "blocked", "parked", "candidate", "promoted", "failed")


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def _kill_checks(hypothesis: dict[str, Any], audit: dict[str, Any], report: dict[str, Any]) -> list[str]:
    metrics = audit.get("metrics") or {}
    triggers: list[str] = []
    incrementality = report.get("incrementality") or {}
    data_quality = report.get("data_quality") or {}
    for rule in hypothesis.get("kill_criteria") or []:
        text = str(rule).lower().replace(" ", "")
        if "turnover>" in text:
            threshold = 0.6
            turnover = _number(metrics.get("turnover"))
            if turnover is not None and turnover > threshold:
                triggers.append(str(rule))
        elif "incremental<" in text or "增量保留<" in text:
            ratio = _number(incrementality.get("effective_ratio"))
            if ratio is None:
                ratio = _number(incrementality.get("ratio"))
            # ratio may be a fraction or percent depending on report writer.
            # effective_ratio is a fraction: 2.45 means 245%, not 2.45%.
            if ratio is not None and ratio < 0.3:
                triggers.append(str(rule))
        elif "coverage<" in text:
            raw_coverage = data_quality.get("coverage")
            if isinstance(raw_coverage, dict):
                raw_coverage = raw_coverage.get("coverage") or raw_coverage.get("valid_obs")
            coverage = _number(raw_coverage)
            if coverage is not None and coverage < 0.7:
                triggers.append(str(rule))
        elif "netspread<=0" in text or "net spread<=0" in text:
            net = _number(metrics.get("spread_net"))
            if net is not None and net <= 0:
                triggers.append(str(rule))
        elif "audithardfail" in text or "audit hard fail" in str(rule).lower():
            if str(audit.get("verdict")) == "fail":
                triggers.append(str(rule))
        elif "direction" in text:
            ic = _number(metrics.get("ic"))
            sign = int(hypothesis.get("expected_sign", 1) or 1)
            if ic is not None and ic * sign < 0:
                triggers.append(str(rule))
    return triggers


def derive(root: Path) -> dict[str, Any]:
    manifest = read_json(root / "manifest.json") or {}
    hypothesis = read_json(root / "hypothesis.json") or {}
    audit = read_json(root / "audit.json") or {}
    payload = read_json(root / "report.json") or {}
    report = payload.get("report") or {}
    rating = payload.get("rating") or report.get("rating") or {}
    decision = read_json(root / "decision.json") or {}
    data_manifest = read_json(root / "data_manifest.json") or {}
    factor_artifact = data_manifest.get("factor_artifact") or {}

    metrics = audit.get("metrics") or {}
    audit_verdict = str(audit.get("verdict") or "pending")
    g3 = str(audit.get("g3") or "pending")
    grade = str(rating.get("grade") or "")
    checks = list(audit.get("checks") or []) + list(audit.get("g3_checks") or [])
    orthogonality = next((item for item in checks if item.get("check_id") == "orthogonality"), None)
    kill_triggered = _kill_checks(hypothesis, audit, report)
    evidence: list[str] = []

    if manifest.get("status") == "failed":
        status, reason = "failed", "runner failed"
    elif not manifest or not audit or not payload:
        status, reason = "needs_fix", "missing experiment artifacts"
        evidence.append("manifest/audit/report missing")
    elif not factor_artifact:
        status, reason = "needs_fix", "data_manifest.factor_artifact is empty"
        evidence.append("code_hash/version not pinned in data_manifest")
    elif orthogonality is None:
        status, reason = "needs_fix", "orthogonality not computed"
        evidence.append("orthogonality.json/check missing")
    elif orthogonality.get("status") == "fail":
        status, reason = "parked", "orthogonality hard fail"
        evidence.append("G0.5 fail")
    elif kill_triggered:
        status, reason = "parked", "pre-registered kill criteria triggered"
        evidence.extend(kill_triggered)
    elif audit_verdict == "fail":
        status, reason = "needs_fix", "audit hard fail"
        evidence.append("audit verdict=fail")
    elif g3 == "fail" or grade == "D":
        status, reason = "parked", "negative experiment"
        evidence.append(f"g3={g3}, grade={grade or 'none'}")
    elif g3 == "pass" and grade in {"A", "B"} and audit_verdict == "pass":
        status, reason = "candidate", "passes gates"
    else:
        status, reason = "needs_fix", "gate or evaluation consistency review required"
        evidence.append(f"audit={audit_verdict}, g3={g3}, grade={grade or 'none'}")

    result = {
        "schema_version": "factor_lab.evaluation.v1",
        "exp_id": root.name,
        "status": status,
        "reason": reason,
        "audit": audit_verdict,
        "g3": g3,
        "rating": grade or None,
        "score": rating.get("score"),
        "kill_criteria_triggered": kill_triggered,
        "evidence": evidence,
        "decision_artifact": str(root / "decision.json") if (root / "decision.json").exists() else None,
        "decision_written": decision.get("decision"),
        "finalized_by": "gate-service",
        "finalized_at": config.now_iso(),
    }
    return result


def finalize(root: Path, *, supervisor_verdict: str | None = None) -> dict[str, Any]:
    result = derive(root)
    if supervisor_verdict:
        result["supervisor_verdict"] = supervisor_verdict
        if supervisor_verdict == "blocked" and result["status"] not in {"failed", "parked"}:
            result["status"] = "blocked"
            result["reason"] = "supervisor blocked"
    write_json_atomic(root / "evaluation.json", result)
    return result
