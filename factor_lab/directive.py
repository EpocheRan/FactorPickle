"""Human-authored research direction; the only input the Miner may not invent."""

from __future__ import annotations

from typing import Any

from .config import DIRECTIVE_PATH, now_iso
from .store import read_json, sha256_json, write_json_atomic


def default_directive() -> dict[str, Any]:
    return {
        "schema_version": "factor_lab.directive.v1",
        "theme": "phase-0 smoke: price/volume",
        "universe": "pool:2022-01-04:200",
        "horizon": 5,
        "groups": 5,
        "cost_bps": 20.0,
        "start": "2019-01-01",
        "end": "2026-11-01",
        "max_trials_per_cycle": 4,
        "max_trials_per_day": 12,
        "avoid_families": [],
        "family_id": "F-phase0-cycle",
        "notes": "Phase 0 smoke cycle; 4 factors, quick profile, no human in the loop.",
        "updated_at": now_iso(),
    }


def ensure_directive() -> dict[str, Any]:
    current = read_json(DIRECTIVE_PATH)
    if isinstance(current, dict) and current.get("theme"):
        return current
    value = default_directive()
    write_json_atomic(DIRECTIVE_PATH, value)
    return value


def load_directive() -> dict[str, Any]:
    return ensure_directive()


def write_directive(payload: dict[str, Any]) -> dict[str, Any]:
    value = dict(payload)
    value.setdefault("schema_version", "factor_lab.directive.v1")
    value["updated_at"] = now_iso()
    value["sha256"] = sha256_json({k: v for k, v in value.items() if k != "sha256"})
    write_json_atomic(DIRECTIVE_PATH, value)
    return value
