"""Role contracts for the Alpha engine.

The behavior files under ``team/behaviors/<agent>/`` are the source of truth.
This module exposes the current contracts to the CLI; it does not keep a second
copy of prompts or forbidden action lists.
"""

from __future__ import annotations

from typing import Any

from factor_lab.alpha.team.behavior import list_behaviors


DETERMINISTIC_ROLES: dict[str, str] = {
    "Runner": "factor artifact + data_interface -> data_bundle/manifest",
    "Research Runtime": "compile/evaluate/backtest -> research_result",
    "Evaluator": "deterministic caveats/next_action; cannot alter verdict",
    "Supervisor": "rounds/budget/stop/reclaim hard limits",
    "Falsifier": "PIT/cost/DSR/gate checks",
    "Registrar": "trial ledger, edge cards, memory",
    "Allocator": "index baseline and validated active weights",
    "ML Runtime": "fit/predict/purged CV/diagnostics",
}

TIERS: list[dict[str, Any]] = [
    {"tier": "L0", "llm_roles": ["pathfinder", "factor-builder"], "reason": "first Edge Card"},
    {"tier": "L1", "llm_roles": ["pathfinder", "factor-builder"], "services": ["Runner", "Research Runtime", "Evaluator", "Supervisor"]},
    {"tier": "L2", "llm_roles": ["pathfinder", "factor-builder", "web-gateway"], "services": ["Memory"]},
    {"tier": "L3", "llm_roles": ["pathfinder", "factor-builder", "ml-planner"], "services": ["ML Runtime"]},
    {"tier": "L4", "llm_roles": ["pathfinder", "factor-builder", "reader"], "services": ["Reader pilot"]},
    {"tier": "L5", "llm_roles": ["pathfinder", "factor-builder"], "services": ["Allocator"]},
]


def role_contracts() -> dict[str, Any]:
    behaviors = list_behaviors()
    llm_roles = {
        row["agent"]: {
            "behavior_hash": row["behavior_hash"],
            "outputs": row["contract"].get("outputs") or [],
            "forbidden": row["contract"].get("forbidden") or [],
            "tier": row["contract"].get("tier"),
            "n_evals": row["n_evals"],
        }
        for row in behaviors
    }
    return {
        "schema": "factor_lab.alpha.roles.v1",
        "llm_roles": llm_roles,
        "deterministic_roles": DETERMINISTIC_ROLES,
        "n_llm_roles": len(llm_roles),
        "tiers": TIERS,
    }


def active_roles(tier: str) -> dict[str, Any]:
    selected = next((item for item in TIERS if item["tier"] == str(tier).upper()), None)
    if selected is None:
        raise ValueError(f"unknown tier: {tier}")
    return selected
