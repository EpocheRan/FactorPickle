"""Supervisor readiness feedback.

When data / environment cannot satisfy a test, the Supervisor writes a concrete
blocker with options instead of silently degrading.  The artifact is designed
to be consumed by a human or by the DSH Supervisor turn.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from factor_lab.core import config
from factor_lab.core.store import write_json_atomic

BLOCKER_PATH = config.CONTROL_DIR / "blocker_feedback.json"


class ReadinessBlocked(RuntimeError):
    pass


def evaluate_data_status(status: dict[str, Any], *, required: tuple[str, ...] = ("market",)) -> dict[str, Any]:
    services = set(status.get("services") or [])
    missing = [service for service in required if service not in services]
    available = bool(status.get("available")) and not missing
    return {
        "schema_version": "factor_lab.readiness.v1",
        "available": available,
        "required_services": list(required),
        "available_services": sorted(services),
        "missing_services": missing,
        "error": status.get("error", ""),
    }


def write_blocker(
    *,
    blocked_by: str,
    evidence: dict[str, Any] | None = None,
    options: list[dict[str, Any]] | None = None,
    recommended: str = "",
    path: Path | None = None,
) -> dict[str, Any]:
    payload = {
        "schema_version": "factor_lab.blocker_feedback.v1",
        "at": config.now_iso(),
        "blocked_by": blocked_by,
        "evidence": evidence or {},
        "options": options or [],
        "recommended": recommended,
        "awaiting_user": True,
        "note": "Supervisor paused the affected branch; it will not silently relax data/test constraints.",
    }
    write_json_atomic(path or BLOCKER_PATH, payload)
    return payload


def require_data(status: dict[str, Any], *, required: tuple[str, ...] = ("market",)) -> dict[str, Any]:
    readiness = evaluate_data_status(status, required=required)
    if readiness["available"]:
        return readiness
    write_blocker(
        blocked_by="data_interface",
        evidence=readiness,
        options=[
            {"option": "configure data_interface", "impact": "enables Runner data access", "cost": "low"},
            {"option": "change factor data contract", "impact": "may use an available service", "cost": "low"},
            {"option": "defer this factor", "impact": "no result; no fake data", "cost": "none"},
        ],
        recommended="configure data_interface",
    )
    raise ReadinessBlocked(
        f"data_interface readiness failed: missing={readiness['missing_services']} error={readiness['error']}"
    )
