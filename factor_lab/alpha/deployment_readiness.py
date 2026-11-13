"""Deployment-readiness state for the Alpha product.

The research engine can produce evidence; it does not silently promote that
evidence into a live account.  This module makes the missing links explicit.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from factor_lab.alpha.evaluation_contract import load_holdout_v2
from factor_lab.alpha.trial_accounting import ledger_snapshot_hash
from factor_lab.core import config


READINESS_SCHEMA = "factor_lab.alpha.deployment_readiness.v1"


def deployment_readiness() -> dict[str, Any]:
    blockers = [
        "no validated/active Edge Card produced by an automated promotion state machine",
        "no long-only portfolio constructor with industry/size neutrality and turnover constraints",
        "no covariance risk model",
        "no capacity/impact model",
        "no execution bridge to backtest/position manager",
        "full-market PIT membership and delisting returns are not implemented",
    ]
    checks = {
        "ledger_snapshot_frozen": ledger_snapshot_hash() is not None,
        "forward_holdout_locked": not bool(load_holdout_v2().get("confirmation_allowed", False)),
        "has_validated_edge_card": False,
        "portfolio_constructor": False,
        "risk_model": False,
        "capacity_impact_model": False,
        "execution_bridge": False,
    }
    return {
        "schema": READINESS_SCHEMA,
        "status": "blocked",
        "ready": False,
        "checks": checks,
        "blockers": blockers,
        "policy": {"holdout_v2": load_holdout_v2()},
        "note": "Research evidence may be complete while deployment remains blocked; never allocate from a Gate pass alone.",
    }
