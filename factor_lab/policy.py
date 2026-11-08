"""Versioned gate policy.

The policy is owned by the Director and only applied by the Auditor.  A policy
change requires a new version and a new hash; prior decisions keep their old
version.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from .config import GATE_POLICY_PATH, now_iso
from .store import read_json, sha256_json, write_json_atomic

DEFAULT_POLICY: dict[str, Any] = {
    "policy_id": "gate-policy",
    "version": "1.0.0",
    "status": "p0-bootstrap",
    "owner": "director",
    "approved_by": "p0-bootstrap",
    "effective_from": "2026-11-08",
    "notes": "P0 default; human sign-off is required before production use.",
    "gate_types": {
        "E0": ["CODE", "MODEL"],
        "E1": ["CODE"],
        "E2": ["MODEL"],
        "G0": ["CODE", "MODEL"],
        "G0.5": ["CODE"],
        "G1": ["MODEL"],
        "G2": ["CODE"],
        "G3": ["CODE", "MODEL"],
        "G4": ["CODE", "HUMAN"],
    },
    "thresholds": {
        "max_abs_rho_hard": 0.9,
        "max_abs_rho_warn": 0.7,
        "rho_rule": "lower_ci>0.9 fail; upper_ci<0.7 pass; crossing warn",
        "min_coverage": 0.6,
        "min_ic": 0.02,
        "max_turnover_5d": 0.6,
        "min_capacity_multiple": 3.0,
        "cost_sensitivity_multiple": 2.0,
        "g4_t_abs": 3.0,
        "max_generations": 2,
        "max_variants_per_parent": 5,
    },
    "adjusted_p": {
        "method": "BH",
        "scope": "bet",
        "q": 0.1,
        "note": "One authoritative adjusted p; G4 uses an absolute threshold, not a second correction.",
    },
    "changelog": [
        {
            "version": "1.0.0",
            "date": "2026-11-08",
            "change": "P0 policy: gate types, one adjusted p, turnover/capacity, single-horse default.",
        }
    ],
}


def default_policy() -> dict[str, Any]:
    policy = deepcopy(DEFAULT_POLICY)
    policy["effective_from"] = now_iso()[:10]
    policy["sha256"] = policy_hash(policy)
    return policy


def policy_hash(policy: dict[str, Any]) -> str:
    payload = {key: value for key, value in policy.items() if key != "sha256"}
    return sha256_json(payload)


def verify_policy(policy: dict[str, Any]) -> tuple[bool, str]:
    expected = policy_hash(policy)
    actual = str(policy.get("sha256", ""))
    return actual == expected, expected


def load_policy() -> dict[str, Any]:
    policy = read_json(GATE_POLICY_PATH)
    if not isinstance(policy, dict):
        raise FileNotFoundError(f"gate policy not found: {GATE_POLICY_PATH}")
    ok, expected = verify_policy(policy)
    if not ok:
        raise ValueError(f"gate policy hash mismatch: stored={policy.get('sha256')} expected={expected}")
    return policy


def write_policy(policy: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = deepcopy(policy or default_policy())
    payload["sha256"] = policy_hash(payload)
    write_json_atomic(GATE_POLICY_PATH, payload)
    return payload


def init_policy(force: bool = False) -> dict[str, Any]:
    if GATE_POLICY_PATH.exists() and not force:
        return load_policy()
    return write_policy(default_policy())
