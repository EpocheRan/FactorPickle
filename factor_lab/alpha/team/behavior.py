"""Current-only behavior loader for the Alpha DSH team.

There is one directory per agent and no version/candidate/history directory.
Git holds behavior history; runtime loads only current files.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from factor_lab.core.store import read_json, sha256_file


BEHAVIOR_SCHEMA = "factor_lab.alpha.behavior_contract.v1"
BEHAVIOR_ROOT = Path(__file__).resolve().parent / "behaviors"
HISTORICAL_NAMES = {"current", "candidate", "legacy", "old", "archive", "changelog", "migration"}


def behavior_dir(agent: str) -> Path:
    name = str(agent).strip().lower()
    if not name or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789-" for char in name):
        raise ValueError(f"invalid agent name: {agent!r}")
    return BEHAVIOR_ROOT / name


def _read_eval_suite(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"eval case must be an object: {path}")
        rows.append(value)
    return rows


def behavior_hash(system_path: Path, contract_path: Path, evals_path: Path) -> str:
    digest = hashlib.sha256()
    for path in (system_path, contract_path, evals_path):
        digest.update(path.read_bytes())
        digest.update(b"\n")
    return digest.hexdigest()[:16]


def load_behavior(agent: str) -> dict[str, Any]:
    path = behavior_dir(agent)
    system_path = path / "system.md"
    contract_path = path / "contract.json"
    evals_path = path / "evals.jsonl"
    for required in (system_path, contract_path, evals_path):
        if not required.exists():
            raise FileNotFoundError(f"behavior file missing: {required}")
    system = system_path.read_text(encoding="utf-8").strip()
    contract = read_json(contract_path)
    if not isinstance(contract, dict) or contract.get("schema") != BEHAVIOR_SCHEMA:
        raise ValueError(f"invalid behavior contract: {contract_path}")
    if contract.get("agent") != path.name:
        raise ValueError(f"behavior contract agent mismatch: {contract_path}")
    evals = _read_eval_suite(evals_path)
    if not evals:
        raise ValueError(f"behavior eval suite is empty: {evals_path}")
    return {
        "agent": path.name,
        "path": str(path),
        "behavior_hash": behavior_hash(system_path, contract_path, evals_path),
        "system_prompt": system,
        "contract": contract,
        "evals": evals,
        "n_evals": len(evals),
    }


def list_behaviors() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not BEHAVIOR_ROOT.exists():
        return rows
    for path in sorted(BEHAVIOR_ROOT.iterdir()):
        if not path.is_dir() or path.name == "__pycache__":
            continue
        if any(item.name in HISTORICAL_NAMES or (item.name.startswith("v") and item.name[1:].isdigit()) for item in path.iterdir()):
            continue
        try:
            rows.append(load_behavior(path.name))
        except (OSError, ValueError, json.JSONDecodeError):
            continue
    return rows


def verify_behaviors(expected_agents: list[str] | None = None) -> dict[str, Any]:
    issues: list[str] = []
    agents = list(expected_agents or [])
    for path in BEHAVIOR_ROOT.iterdir() if BEHAVIOR_ROOT.exists() else []:
        if not path.is_dir() or path.name == "__pycache__":
            continue
        bad = [
            child.name
            for child in path.iterdir()
            if child.name in HISTORICAL_NAMES or (child.name.startswith("v") and child.name[1:].isdigit())
        ]
        if bad:
            issues.append(f"{path.name}: historical/version entries: {bad}")
    loaded = list_behaviors()
    found = {row["agent"] for row in loaded}
    for agent in agents:
        if agent not in found:
            issues.append(f"missing behavior: {agent}")
    for row in loaded:
        contract = row["contract"]
        if not contract.get("forbidden"):
            issues.append(f"{row['agent']}: no forbidden actions")
        if not row.get("system_prompt"):
            issues.append(f"{row['agent']}: empty system prompt")
    return {
        "ok": not issues,
        "issues": issues,
        "agents": sorted(found),
        "n_agents": len(found),
        "behaviors": [
            {
                "agent": row["agent"],
                "behavior_hash": row["behavior_hash"],
                "n_evals": row["n_evals"],
            }
            for row in loaded
        ],
    }
