"""Portable DSH team bootstrap for the Alpha research engine.

The agent runtime belongs to DeepSeek Harness.  This module owns only the
portable roster, validation, spawn instructions and session brief.  A new DSH
session can regenerate the same plan from repository files without any prior
chat history.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from factor_lab.alpha.factor_library import library_summary
from factor_lab.alpha.memory import AlphaMemory
from factor_lab.alpha.team.behavior import load_behavior, verify_behaviors
from factor_lab.core import config
from factor_lab.core.store import read_json


TEAM_SCHEMA = "factor_lab.alpha.team.v1"


def roles_path() -> Path:
    return Path(__file__).resolve().parent / "roles.json"


def load_roster(path: str | Path | None = None) -> dict[str, Any]:
    target = Path(path) if path is not None else roles_path()
    raw = read_json(target)
    if not isinstance(raw, dict):
        raise ValueError(f"invalid team roster: {target}")
    if raw.get("schema") != TEAM_SCHEMA:
        raise ValueError(f"unsupported team schema: {raw.get('schema')}")
    return raw


def validate_roster(path: str | Path | None = None) -> dict[str, Any]:
    roster = load_roster(path)
    roles = roster.get("roles")
    if not isinstance(roles, list) or not roles:
        raise ValueError("team roster requires roles")
    names: list[str] = []
    issues: list[str] = []
    for role in roles:
        if not isinstance(role, dict):
            issues.append("role must be an object")
            continue
        name = str(role.get("name") or "").strip()
        if not name:
            issues.append("role missing name")
            continue
        if name in names:
            issues.append(f"duplicate role: {name}")
        names.append(name)
        if role.get("type") != "llm":
            issues.append(f"{name}: only llm roles belong in the team roster")
        if not role.get("behavior"):
            issues.append(f"{name}: missing behavior path")
            continue
        try:
            behavior = load_behavior(name)
        except (OSError, ValueError) as exc:
            issues.append(f"{name}: behavior invalid: {exc}")
            continue
        if not behavior["contract"].get("forbidden"):
            issues.append(f"{name}: behavior has no forbidden actions")
    if roster.get("lead") != "lead":
        issues.append("lead must be 'lead'")
    expected = int((roster.get("verification") or {}).get("required_team_size") or 0)
    if expected != len(roles):
        issues.append(f"required_team_size {expected} != roster size {len(roles)}")
    behavior_check = verify_behaviors(sorted(names))
    issues.extend(behavior_check["issues"])
    return {
        "ok": not issues,
        "issues": issues,
        "team_id": roster.get("team_id"),
        "lead": roster.get("lead"),
        "roles": [role.get("name") for role in roles if isinstance(role, dict)],
        "n_roles": len(names),
        "behaviors": behavior_check["behaviors"],
        "deterministic_services": roster.get("deterministic_services") or [],
    }


def bootstrap_plan(path: str | Path | None = None) -> dict[str, Any]:
    roster = load_roster(path)
    check = validate_roster(path)
    if not check["ok"]:
        raise ValueError("; ".join(check["issues"]))
    members = []
    for role in roster["roles"]:
        behavior = load_behavior(role["name"])
        members.append(
            {
                "target": role["name"],
                "display_name": role.get("display_name"),
                "tier": role.get("tier"),
                "behavior_hash": behavior["behavior_hash"],
                "prompt": behavior["system_prompt"],
                "outputs": behavior["contract"].get("outputs") or [],
                "forbidden": behavior["contract"].get("forbidden") or [],
                "n_evals": behavior["n_evals"],
            }
        )
    return {
        "schema": "factor_lab.alpha.team_plan.v1",
        "team_id": roster["team_id"],
        "lead": roster["lead"],
        "members": members,
        "verification": roster.get("verification") or {},
        "deterministic_services": roster.get("deterministic_services") or [],
        "steps": [
            "run `python -m factor_lab doctor`",
            "use `list_agents` to inspect current DSH teammates",
            "spawn every missing member with `spawn_teammate` and the prompt below",
            "use `list_agents` again and verify all members are running or inactive",
            "run `python -m factor_lab alpha team verify` as the static roster check",
            "start research with `python -m factor_lab alpha campaign ...` or an L0 cycle",
        ],
        "portable": True,
        "source": str(roles_path()),
    }


def session_brief() -> dict[str, Any]:
    memory = AlphaMemory()
    campaigns = []
    campaign_root = config.LAB_ROOT / "alpha" / "campaigns"
    if campaign_root.exists():
        for path in sorted(campaign_root.glob("*/campaign.json")):
            payload = read_json(path)
            if isinstance(payload, dict):
                campaigns.append(
                    {
                        "campaign_id": payload.get("campaign_id"),
                        "objective": payload.get("objective"),
                        "state": payload.get("state"),
                        "n_rounds": payload.get("n_rounds"),
                    }
                )
    return {
        "schema": "factor_lab.alpha.session_brief.v1",
        "lab_root": str(config.LAB_ROOT),
        "team": validate_roster(),
        "behaviors": verify_behaviors(),
        "memory": memory.summary(),
        "factor_library": library_summary(),
        "campaigns": campaigns[-10:],
        "commands": [
            "python -m factor_lab doctor",
            "python -m factor_lab alpha team verify",
            "python -m factor_lab alpha memory",
            "python -m factor_lab alpha library",
            "python -m factor_lab alpha roles",
        ],
    }
