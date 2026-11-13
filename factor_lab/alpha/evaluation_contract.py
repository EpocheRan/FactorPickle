"""First-class evaluation contract for Alpha rounds.

A round is only reproducible when its evaluation window, universe, horizon,
cost model, trial population and pre-registration/ledger snapshots are written
next to the result.  This module is the shared constructor/validator; it never
evaluates a factor.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from factor_lab.core import config


EVALUATION_CONTRACT_SCHEMA = "factor_lab.alpha.evaluation_contract.v1"
HOLDOUT_V2_SCHEMA = "factor_lab.alpha.holdout_v2.v1"
HOLDOUT_V2_START = "2026-11-02"


class HoldoutLockedError(ValueError):
    """Raised when an evaluation tries to consume the frozen forward holdout."""


def holdout_v2_path() -> Path:
    return Path(config.ALPHA_POLICY_DIR) / "holdout_v2.json"


def load_holdout_v2() -> dict[str, Any]:
    path = holdout_v2_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"holdout_v2 policy is missing: {path}") from exc
    if payload.get("schema") != HOLDOUT_V2_SCHEMA:
        raise ValueError(f"unsupported holdout_v2 schema: {payload.get('schema')}")
    return payload


def _ledger_snapshot_hash() -> str | None:
    try:
        from factor_lab.alpha.trial_accounting import ledger_snapshot_hash

        return ledger_snapshot_hash()
    except Exception:  # noqa: BLE001
        return None


def _data_manifest_hash(manifest: dict[str, Any] | None) -> str | None:
    if not manifest:
        return None
    payload = json.dumps(manifest, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def assert_holdout_allowed(evaluation_start: str, evaluation_end: str) -> None:
    holdout = load_holdout_v2()
    start = str(evaluation_start or "")
    end = str(evaluation_end or "")
    if end and end >= str(holdout.get("holdout_start")):
        if not bool(holdout.get("confirmation_allowed", False)):
            raise HoldoutLockedError(
                "evaluation overlaps the frozen forward holdout "
                f"{holdout.get('holdout_start')}: confirmation is not allowed yet "
                f"({holdout.get('reason')})"
            )


def build_evaluation_contract(
    *,
    evaluation_start: str,
    evaluation_end: str,
    universe: str,
    size_screen: str,
    horizon: int,
    cost: dict[str, Any],
    n_eff: int | None,
    n_trials: int | None,
    hypothesis_count: int,
    pre_registration_hash: str | None,
    data_manifest: dict[str, Any] | None = None,
    pool_mode: str = "ex_ante_top1000",
    price_basis: str = "pit_cumulative_adjusted_hfq",
    tradability: str = "partial_ohlcv_mask",
) -> dict[str, Any]:
    holdout = load_holdout_v2()
    try:
        from factor_lab.alpha.data import load_holdout

        historical = load_holdout()
    except Exception:  # noqa: BLE001
        historical = None
    if historical is not None and str(evaluation_end) > str(historical.dev_end):
        raise HoldoutLockedError(
            f"evaluation window ends {evaluation_end}, after historical dev_end {historical.dev_end}; "
            "the locked holdout is not available to the research pipeline"
        )
    is_holdout = str(evaluation_end) >= str(holdout.get("holdout_start"))
    if is_holdout and not bool(holdout.get("confirmation_allowed", False)):
        raise HoldoutLockedError(
            "evaluation overlaps the frozen forward holdout "
            f"{holdout.get('holdout_start')}: confirmation is not allowed yet "
            f"({holdout.get('reason')})"
        )
    return {
        "schema": EVALUATION_CONTRACT_SCHEMA,
        "evidence_kind": "holdout" if is_holdout else "development_evaluation",
        "evaluation_start": str(evaluation_start),
        "evaluation_end": str(evaluation_end),
        "universe": str(universe),
        "size_screen": str(size_screen),
        "pool_mode": str(pool_mode),
        "horizon": int(horizon),
        "cost": dict(cost),
        "n_eff": None if n_eff is None else int(n_eff),
        "n_trials": None if n_trials is None else int(n_trials),
        "hypothesis_count": int(hypothesis_count),
        "pre_registration_hash": pre_registration_hash,
        "ledger_snapshot_hash": _ledger_snapshot_hash(),
        "data_manifest_hash": _data_manifest_hash(data_manifest),
        "price_basis": str(price_basis),
        "tradability": str(tradability),
        "known_gaps": [
            "full_market_pit_pool_not_implemented",
            "delisting_returns_not_implemented",
            "st_new_stock_sell_side_not_fully_modeled",
            "capacity_impact_model_not_implemented",
        ],
        "holdout_v2": {
            "start": holdout.get("holdout_start"),
            "state": holdout.get("state"),
            "confirmation_allowed": bool(holdout.get("confirmation_allowed", False)),
        },
        "created_at": config.now_iso(),
    }


def validate_evaluation_contract(payload: dict[str, Any] | None) -> dict[str, Any]:
    issues: list[str] = []
    if not isinstance(payload, dict):
        return {"ok": False, "issues": ["evaluation_contract is missing"]}
    if payload.get("schema") != EVALUATION_CONTRACT_SCHEMA:
        issues.append(f"schema must be {EVALUATION_CONTRACT_SCHEMA}")
    for key in ("evaluation_start", "evaluation_end", "universe", "size_screen", "horizon", "cost", "price_basis", "tradability"):
        if payload.get(key) in (None, "", {}):
            issues.append(f"{key} is required")
    if not isinstance(payload.get("cost"), dict):
        issues.append("cost must be an object")
    if payload.get("evidence_kind") not in {"development_evaluation", "holdout"}:
        issues.append("evidence_kind must be development_evaluation or holdout")
    if payload.get("evidence_kind") == "holdout" and not (payload.get("holdout_v2") or {}).get("confirmation_allowed"):
        issues.append("holdout evidence requires confirmation_allowed=true")
    return {"ok": not issues, "issues": issues}
