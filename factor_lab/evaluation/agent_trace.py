"""Agent trace writer for the factor research chain."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from factor_lab.core import config
from factor_lab.core.contracts import ROLE_CONTRACTS
from factor_lab.core.store import read_json, write_json_atomic

TRACE_NAME = "agent_trace.json"


def write_trace(
    root: Path,
    *,
    input_payload: dict[str, Any],
    stages: list[dict[str, Any]],
    verdict: str = "",
) -> dict[str, Any]:
    normalized = []
    for stage in stages:
        role = str(stage.get("role") or "").lower().replace(" ", "-")
        contract = ROLE_CONTRACTS.get(role, {})
        normalized.append(
            {
                "role": role,
                "status": str(stage.get("status") or "used"),
                "provenance": str(stage.get("provenance") or ""),
                "thought": str(stage.get("thought") or ""),
                "next": list(contract.get("next") or []),
                **{key: value for key, value in stage.items() if key not in {"role", "status", "provenance", "thought"}},
            }
        )
    payload = {
        "schema_version": "factor_lab.agent_trace.v1",
        "input": input_payload,
        "stages": normalized,
        "verdict": verdict,
        "created_at": config.now_iso(),
    }
    write_json_atomic(root / TRACE_NAME, payload)
    return payload


def read_trace(root: Path) -> dict[str, Any]:
    raw = read_json(root / TRACE_NAME)
    return raw if isinstance(raw, dict) else {}
