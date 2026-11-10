"""Durable research-role contracts.

Contracts are intentionally small: they define who may emit which stage, what
must be present, and what the next stage is allowed to consume.  They do not
replace the Gate; they stop an agent from silently producing an incomplete
hand-off.
"""

from __future__ import annotations

from typing import Any

ROLE_CONTRACTS: dict[str, dict[str, Any]] = {
    "pathfinder": {
        "required": ("action", "reason"),
        "optional": ("factor_idea", "ml_idea", "escape_mode", "novelty_vector", "informed_by", "differentiation"),
        "next": ("web-gateway", "factor-builder", "ml-planner"),
        "write_scope": (),
    },
    "web-gateway": {
        "required": ("findings", "sources"),
        "optional": ("discovery_map", "caveats", "mode"),
        "next": ("pathfinder",),
        "write_scope": ("output/factor_lab/discovery/",),
    },
    "factor-builder": {
        "required": ("family", "factor_id", "version", "code", "spec"),
        "optional": ("tests", "path", "code_hash"),
        "next": ("runner",),
        "write_scope": ("output/factor_lab/factors/generated/",),
    },
    "runner": {
        "required": ("factor_id", "data_path", "codes"),
        "optional": ("exp_id", "run_id", "rows", "blockers"),
        "next": ("evaluator",),
        "write_scope": ("output/factor_lab/experiments/",),
    },
    "evaluator": {
        "required": ("exp_id", "rating", "audit", "next_action"),
        "optional": ("g3", "consistency_issues", "caveats", "reason"),
        "next": ("supervisor",),
        "write_scope": (),
    },
    "ml-planner": {
        "required": ("model", "features", "target", "walk_forward"),
        "optional": ("cost_bps", "kill_criteria", "reason"),
        "next": ("ml-engine", "runner"),
        "write_scope": (),
    },
    "ml-monitor": {
        "required": ("status", "leakage_risk", "over_search_risk"),
        "optional": ("cost_rmb", "findings", "required_actions"),
        "next": ("supervisor",),
        "write_scope": (),
    },
    "supervisor": {
        "required": ("verdict", "findings", "next_gate"),
        "optional": ("required_actions", "forbidden_actions", "evidence"),
        "next": ("pathfinder", "lead"),
        "write_scope": ("output/factor_lab/governance/", "output/factor_lab/experiments/"),
    },
}


def validate(role: str, payload: dict[str, Any]) -> dict[str, Any]:
    contract = ROLE_CONTRACTS.get(role)
    if contract is None:
        return {"ok": False, "role": role, "missing": ["unknown role"], "contract": None}
    missing = [key for key in contract["required"] if payload.get(key) in (None, "", [], {})]
    return {
        "ok": not missing,
        "role": role,
        "missing": missing,
        "next": list(contract["next"]),
        "contract": contract,
    }
