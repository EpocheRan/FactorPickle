"""Decision brief and decision record.

The brief is a compact, bounded summary.  The recommendation is a deterministic
P0 rule; in the full team the Builder writes the recommendation and the Auditor
only verifies numbers and risk completeness.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import config
from .ledger import Ledger
from .store import read_json, write_json_atomic


def _num(value: Any, digits: int = 3) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "n/a"
    if not (number == number):
        return "n/a"
    return f"{number:.{digits}f}"


def _pct(value: Any, digits: int = 2) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "n/a"
    if not (number == number):
        return "n/a"
    return f"{number * 100:+.{digits}f}%"


def _risk_list(report: dict[str, Any], audit: dict[str, Any]) -> list[str]:
    risks: list[str] = []
    turnover = report.get("turnover")
    max_turnover = float((audit.get("policy") or {}).get("max_turnover_5d", 0.6))
    if turnover is not None:
        try:
            if float(turnover) > max_turnover:
                risks.append(f"turnover {float(turnover):.0%} > cap {max_turnover:.0%}")
        except (TypeError, ValueError):
            pass
    capacity = report.get("capacity") or {}
    multiple = None
    if isinstance(capacity, dict):
        for key in ("multiple", "capacity_multiple", "ratio"):
            if key in capacity:
                multiple = capacity.get(key)
                break
    if multiple is None:
        risks.append("capacity not measured")
    else:
        try:
            if float(multiple) < 3.0:
                risks.append(f"capacity {float(multiple):.1f}x < 3x")
        except (TypeError, ValueError):
            pass
    for check in audit.get("checks") or []:
        if isinstance(check, dict) and check.get("status") == "fail":
            risks.append(f"audit fail: {check.get('check_id')}")
    if not risks:
        risks.append("no material risk flagged by P0 checks")
    return risks[:3]


def build_brief(root: Path) -> dict[str, Any]:
    manifest = read_json(root / "manifest.json") or {}
    payload = read_json(root / "report.json") or {}
    audit = read_json(root / "audit.json") or {}
    report = payload.get("report") or {}
    hypothesis = read_json(root / "hypothesis.json") or {}
    rating = payload.get("rating") or report.get("rating") or {}

    g3 = str(audit.get("g3") or "warn")
    verdict = str(audit.get("verdict") or "warn")
    if verdict == "fail":
        gate = "G2 fail"
    elif g3 == "fail":
        gate = "G3 fail"
    elif g3 == "warn":
        gate = "G3 boundary"
    else:
        gate = "G3 pass"

    if verdict == "fail":
        recommend = "repair"
    elif str(rating.get("grade")) == "D":
        recommend = "park"
    elif g3 == "fail":
        recommend = "iterate"
    elif g3 == "warn":
        recommend = "iterate"
    else:
        recommend = "promote to G4"

    metrics = audit.get("metrics") or {}
    brief = {
        "schema_version": config.SCHEMA_VERSION,
        "exp_id": root.name,
        "candidate": manifest.get("factor_id"),
        "bet": hypothesis.get("bet") or "unassigned",
        "gate": gate,
        "effect": {
            "ic": _num(metrics.get("ic"), 4),
            "ic_t": _num(metrics.get("ic_t"), 2),
            "net_period": _pct(metrics.get("spread_net"), 3),
            "turnover": _num(metrics.get("turnover"), 3),
        },
        "incremental": {
            "max_rho": metrics.get("max_rho"),
            "note": "P0: orthogonality tool pending; manual rho allowed only for process checks.",
        },
        "trials": {
            "n": audit.get("n_trials"),
            "q": audit.get("q_value"),
            "q_note": audit.get("q_note"),
        },
        "cost": {
            "duration_sec": manifest.get("duration_sec"),
            "profile": (manifest.get("params") or {}).get("profile"),
        },
        "risks": _risk_list(report, audit),
        "recommend": recommend,
        "drill_down": str(root / "report.html"),
        "verdict": verdict,
        "g3": g3,
    }

    # Keep the brief under 2KB.  Drop optional prose first, then truncate risks.
    while len(json.dumps(brief, ensure_ascii=False).encode("utf-8")) > 2048:
        if brief["incremental"].get("note"):
            brief["incremental"]["note"] = ""
        elif len(brief["risks"]) > 1:
            brief["risks"].pop()
        else:
            break
    write_json_atomic(root / "decision_brief.json", brief)
    return brief


def write_decision(root: Path, brief: dict[str, Any], audit: dict[str, Any]) -> dict[str, Any]:
    recommend = str(brief.get("recommend"))
    decision = {
        "repair": "repair",
        "iterate": "iterate",
        "park": "park",
        "promote to G4": "promote",
    }.get(recommend, "park")
    reason = f"P0 deterministic decision: {recommend}; gate={brief.get('gate')}; audit={audit.get('verdict')}"
    record = {
        "schema_version": config.SCHEMA_VERSION,
        "exp_id": root.name,
        "decision": decision,
        "recommend": recommend,
        "gate": brief.get("gate"),
        "reason": reason,
        "policy_version": (audit.get("policy") or {}).get("version"),
        "human_escalation": None,
        "created_at": config.now_iso(),
    }
    if audit.get("verdict") == "fail":
        record["human_escalation"] = {
            "trigger": "audit_fail",
            "reason": "G2 hard failure; repair before any promotion.",
        }
    write_json_atomic(root / "decision.json", record)
    ledger = Ledger()
    ledger.append(
        "decision_written",
        {
            "exp_id": root.name,
            "decision": decision,
            "recommend": recommend,
            "gate": brief.get("gate"),
            "path": str(root / "decision.json"),
        },
    )
    ledger.write_summary()
    return record
