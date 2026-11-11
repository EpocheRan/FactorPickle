"""Role contracts for the Alpha research engine.

Only the five LLM roles in this module are model-facing.  Runner, Research
Runtime, Evaluator, Supervisor, Falsifier, Registrar and Allocator are
deterministic services; they are not prompts and must not be allowed to
rewrite evidence or gate verdicts.
"""

from __future__ import annotations

from typing import Any


LLM_ROLES: dict[str, dict[str, Any]] = {
    "PathFinder": {
        "outputs": ["research_question", "mechanism_priority", "campaign_decision"],
        "boundary": "no data access, no code execution, no gate verdict",
    },
    "Web Gateway": {
        "outputs": ["discovery_brief", "discovery_map", "web_digest", "sources"],
        "boundary": "no factor/model judgment; no raw HTML into PathFinder",
    },
    "ML Planner": {
        "outputs": ["ml_plan", "model_card_interpretation", "refine_stop"],
        "boundary": "runtime fits/evaluates; planner cannot alter metrics",
    },
    "FactorBuilder": {
        "outputs": ["factor_artifact", "spec", "tests", "manifest"],
        "boundary": "writes versioned Factor Library only; cannot allocate capital",
    },
    "Reader": {
        "outputs": ["reader_json", "evidence_pages"],
        "boundary": "extraction only; no investment conclusion",
    },
}

DETERMINISTIC_ROLES: dict[str, str] = {
    "Runner": "factor artifact + data_interface -> data_bundle/manifest",
    "Research Runtime": "compile/evaluate/backtest -> research_result",
    "Evaluator": "deterministic caveats/next_action; cannot alter verdict",
    "Supervisor": "rounds/budget/stop/reclaim hard limits",
    "Falsifier": "PIT/cost/DSR/gate checks",
    "Registrar": "trial ledger, edge cards, memory",
    "Data Steward": "PIT/coverage/dual-source validation",
    "Allocator": "index baseline and validated active weights",
    "ML Runtime": "fit/predict/purged CV/diagnostics",
}

TIERS: list[dict[str, Any]] = [
    {"tier": "L0", "llm_roles": ["PathFinder", "FactorBuilder"], "reason": "first Edge Card"},
    {"tier": "L1", "llm_roles": ["PathFinder", "FactorBuilder"], "services": ["Runner", "Research Runtime", "Evaluator", "Supervisor"]},
    {"tier": "L2", "llm_roles": ["PathFinder", "FactorBuilder", "Web Gateway"], "services": ["Memory"]},
    {"tier": "L3", "llm_roles": ["PathFinder", "FactorBuilder", "ML Planner"], "services": ["ML Runtime"]},
    {"tier": "L4", "llm_roles": ["PathFinder", "FactorBuilder", "Reader"], "services": ["Reader pilot"]},
    {"tier": "L5", "llm_roles": ["PathFinder", "FactorBuilder"], "services": ["Allocator"]},
]


def role_contracts() -> dict[str, Any]:
    return {
        "schema": "factor_lab.alpha.roles.v1",
        "llm_roles": LLM_ROLES,
        "deterministic_roles": DETERMINISTIC_ROLES,
        "n_llm_roles": len(LLM_ROLES),
        "tiers": TIERS,
    }


def active_roles(tier: str) -> dict[str, Any]:
    selected = next((item for item in TIERS if item["tier"] == str(tier).upper()), None)
    if selected is None:
        raise ValueError(f"unknown tier: {tier}")
    return selected
