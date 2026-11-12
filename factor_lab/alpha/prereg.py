"""Deterministic pre-registration validation and freeze for Alpha campaigns.

The model-facing PathFinder proposes the hypotheses and exact DSL expressions.
This module only validates them (no evaluation, no labels) and writes a frozen,
hash-addressed copy before the deterministic Runner evaluates anything.  It is
a governance service, not a factor generator.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from factor_lab.alpha.data_axes import catalog
from factor_lab.alpha.memory import canonical_expression
from factor_lab.alpha.validation import validate
from factor_lab.core import config
from factor_lab.core.store import sha256_json, write_json_atomic


PRE_REG_SCHEMA = "factor_lab.alpha.pre_registration.v1"
FROZEN_SCHEMA = "factor_lab.alpha.pre_registration_frozen.v1"
BASE_FIELDS = {"open", "high", "low", "close", "volume", "amount", "turnover", "float_mv", "total_mv"}
FORBIDDEN_TOKENS = {"forward", "future", "label", "target", "ret_fwd"}


def allowed_fields() -> set[str]:
    payload = catalog()
    return set(payload["fields"].get("p0", [])) | set(payload["fields"].get("p1", [])) | BASE_FIELDS


def validate_pre_registration(payload: dict[str, Any], *, campaign_id: str | None = None) -> dict[str, Any]:
    issues: list[str] = []
    warnings: list[str] = []
    if not isinstance(payload, dict):
        return {"ok": False, "issues": ["pre-registration must be an object"], "warnings": []}
    if payload.get("schema") != PRE_REG_SCHEMA:
        issues.append(f"schema must be {PRE_REG_SCHEMA}")
    if campaign_id and str(payload.get("campaign_id") or "") != campaign_id:
        issues.append(f"campaign_id must be {campaign_id}")
    families = payload.get("families") or []
    if not isinstance(families, list) or not families:
        issues.append("at least one family is required")
    allowed = allowed_fields()
    family_ids: set[str] = set()
    expressions: set[str] = set()
    n_candidates = 0
    for family in families:
        if not isinstance(family, dict):
            issues.append("family must be an object")
            continue
        family_id = str(family.get("family_id") or "")
        if not family_id:
            issues.append("family_id is required")
        elif family_id in family_ids:
            issues.append(f"duplicate family_id: {family_id}")
        family_ids.add(family_id)
        if not family.get("pre_registered"):
            # PathFinder files may omit the flag; the freeze sets it.
            family["pre_registered"] = True
        candidates = family.get("candidates") or []
        if not isinstance(candidates, list) or not candidates:
            issues.append(f"{family_id}: at least one candidate is required")
        for candidate in candidates:
            if not isinstance(candidate, dict):
                issues.append(f"{family_id}: candidate must be an object")
                continue
            n_candidates += 1
            candidate_id = str(candidate.get("candidate_id") or "")
            if not candidate_id:
                issues.append(f"{family_id}: candidate_id is required")
            expression = str(candidate.get("expression") or "").strip()
            if not expression:
                issues.append(f"{family_id}/{candidate_id}: expression is required")
                continue
            if any(token in expression.lower() for token in FORBIDDEN_TOKENS):
                issues.append(f"{family_id}/{candidate_id}: expression contains a label/forward token")
            report = validate(expression)
            if not report.ok:
                issues.append(f"{family_id}/{candidate_id}: invalid DSL: {report.issues}")
            unknown = sorted({field for field in report.fields if field not in allowed})
            if unknown:
                issues.append(f"{family_id}/{candidate_id}: fields not admitted for P0/P1: {unknown}")
            canonical = canonical_expression(expression)
            if canonical in expressions:
                issues.append(f"{family_id}/{candidate_id}: duplicate canonical expression")
            expressions.add(canonical)
            if str(candidate.get("expected_sign") or "positive") != "positive":
                warnings.append(f"{family_id}/{candidate_id}: expected_sign is not positive after orientation")
            if not candidate.get("kill_criteria"):
                issues.append(f"{family_id}/{candidate_id}: kill_criteria is required")
    if n_candidates < 1:
        issues.append("pre-registration has no candidates")
    return {
        "ok": not issues,
        "issues": issues,
        "warnings": warnings,
        "n_families": len(family_ids),
        "n_candidates": n_candidates,
        "families": sorted(family_ids),
        "expressions": sorted(expressions),
    }


def freeze_pre_registration(
    payload: dict[str, Any],
    *,
    campaign_id: str,
    path: str | Path,
) -> dict[str, Any]:
    report = validate_pre_registration(payload, campaign_id=campaign_id)
    if not report["ok"]:
        raise ValueError("pre-registration invalid: " + "; ".join(report["issues"]))
    frozen = json.loads(json.dumps(payload, ensure_ascii=False, default=str))
    frozen["schema"] = FROZEN_SCHEMA
    frozen["campaign_id"] = campaign_id
    frozen["pre_registration_hash"] = sha256_json(
        {
            "campaign_id": campaign_id,
            "families": [
                {
                    "family_id": family["family_id"],
                    "candidates": [
                        {
                            "candidate_id": candidate["candidate_id"],
                            "expression": candidate["expression"],
                            "canonical_expression": canonical_expression(candidate["expression"]),
                            "expected_sign": candidate.get("expected_sign", "positive"),
                        }
                        for candidate in family.get("candidates") or []
                    ],
                }
                for family in frozen["families"]
            ],
        }
    )
    frozen["frozen_at"] = config.now_iso()
    frozen["frozen_by"] = "lead"
    frozen["evaluation_allowed"] = True
    frozen["report"] = {
        "n_families": report["n_families"],
        "n_candidates": report["n_candidates"],
        "families": report["families"],
        "warnings": report["warnings"],
    }
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(target, frozen)
    frozen["path"] = str(target)
    return frozen


def verify_frozen(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if payload.get("schema") != FROZEN_SCHEMA:
        raise ValueError("not a frozen pre-registration")
    report = validate_pre_registration({**payload, "schema": PRE_REG_SCHEMA}, campaign_id=payload.get("campaign_id"))
    stored = payload.get("pre_registration_hash")
    recomputed = sha256_json(
        {
            "campaign_id": payload.get("campaign_id"),
            "families": [
                {
                    "family_id": family["family_id"],
                    "candidates": [
                        {
                            "candidate_id": candidate["candidate_id"],
                            "expression": candidate["expression"],
                            "canonical_expression": candidate.get("canonical_expression") or canonical_expression(candidate["expression"]),
                            "expected_sign": candidate.get("expected_sign", "positive"),
                        }
                        for candidate in family.get("candidates") or []
                    ],
                }
                for family in payload.get("families") or []
            ],
        }
    )
    return {"ok": bool(report["ok"]) and stored == recomputed, "report": report, "hash": stored, "recomputed_hash": recomputed}


def find_frozen_candidate(expression: str, root: str | Path | None = None) -> dict[str, Any] | None:
    """Return frozen pre-registration evidence for a canonical expression."""
    import json as _json
    from factor_lab.alpha.memory import canonical_expression as _canonical
    from factor_lab.core import config as _config

    target = _canonical(expression)
    lab_root = Path(root) if root is not None else Path(_config.LAB_ROOT)
    for path in sorted((lab_root / "alpha" / "campaigns").glob("*/team/pre_registration.frozen.json")):
        try:
            payload = _json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        for family in payload.get("families") or []:
            for candidate in family.get("candidates") or []:
                candidate_expression = str(candidate.get("expression") or "")
                if candidate_expression and _canonical(candidate_expression) == target:
                    return {
                        "campaign_id": payload.get("campaign_id") or path.parent.parent.name,
                        "family_id": family.get("family_id"),
                        "candidate_id": candidate.get("candidate_id"),
                        "expression": candidate_expression,
                        "path": str(path),
                        "pre_registration_hash": payload.get("pre_registration_hash"),
                        "hash_ok": verify_frozen(path).get("ok"),
                    }
    return None
