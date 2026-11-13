"""Deterministic governance primitives for the Alpha research engine.

This module makes policy and holdout provenance explicit. It does not decide
research outcomes; it only hashes policy files, locates frozen
pre-registrations and records holdout consumption.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

from factor_lab.core import config


POLICY_FILES = ("families.json", "gates.json", "universe.json", "holdout.json", "holdout_v2.json", "mandate.json")


def policy_dir() -> Path:
    return Path(config.ALPHA_POLICY_DIR)


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.name.encode("utf-8"))
    digest.update(path.read_bytes())
    return digest.hexdigest()


def policy_snapshot() -> dict[str, Any]:
    files = {name: _file_hash(policy_dir() / name) for name in POLICY_FILES if (policy_dir() / name).exists()}
    payload = json.dumps(files, sort_keys=True, separators=(",", ":"))
    families = {}
    try:
        families = json.loads((policy_dir() / "families.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        pass
    return {
        "schema": "factor_lab.alpha.policy_snapshot.v1",
        "policy_version": families.get("policy_version"),
        "policy_hash": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
        "files": files,
    }


def git_commit() -> str | None:
    try:
        value = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        return value.stdout.strip() or None if value.returncode == 0 else None
    except Exception:  # noqa: BLE001
        return None


def frozen_candidate_index(root: str | Path | None = None) -> dict[str, dict[str, Any]]:
    """Map canonical expression -> frozen pre-registration evidence."""
    from factor_lab.alpha.memory import canonical_expression
    from factor_lab.alpha.prereg import verify_frozen

    lab_root = Path(root) if root is not None else Path(config.LAB_ROOT)
    index: dict[str, dict[str, Any]] = {}
    for path in sorted((lab_root / "alpha" / "campaigns").glob("*/team/pre_registration.frozen.json")):
        payload = None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            check = verify_frozen(path)
        except Exception:  # noqa: BLE001
            check = {"ok": False}
        if not isinstance(payload, dict):
            continue
        campaign_id = str(payload.get("campaign_id") or path.parent.parent.name)
        for family in payload.get("families") or []:
            for candidate in family.get("candidates") or []:
                expression = str(candidate.get("expression") or "")
                if not expression:
                    continue
                index[canonical_expression(expression)] = {
                    "campaign_id": campaign_id,
                    "family_id": family.get("family_id"),
                    "candidate_id": candidate.get("candidate_id"),
                    "expression": expression,
                    "path": str(path),
                    "pre_registration_hash": payload.get("pre_registration_hash"),
                    "hash_ok": bool(check.get("ok")),
                }
    return index


def find_frozen_candidate(expression: str, root: str | Path | None = None) -> dict[str, Any] | None:
    from factor_lab.alpha.memory import canonical_expression

    return frozen_candidate_index(root).get(canonical_expression(expression))


def holdout_use_log() -> Path:
    path = Path(config.LAB_ROOT) / "alpha" / "holdout_uses.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _existing_confirmation_uses() -> list[dict[str, Any]]:
    """Build a read-only audit view of confirmations that already consumed holdout."""
    rows: list[dict[str, Any]] = []
    for path in sorted((Path(config.LAB_ROOT) / "alpha" / "confirm").glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        dataset = payload.get("dataset") or {}
        rows.append(
            {
                "source": "historical_confirm_artifact",
                "path": str(path),
                "expression": payload.get("expression"),
                "holdout_start": dataset.get("start"),
                "holdout_end": dataset.get("end"),
                "data_hash": dataset.get("data_hash"),
                "evidence_status": payload.get("evidence_status"),
            }
        )
    return rows


def holdout_uses() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    log = holdout_use_log()
    if log.exists():
        try:
            for line in log.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    value = json.loads(line)
                    if isinstance(value, dict):
                        rows.append(value)
        except Exception:  # noqa: BLE001
            pass
    seen = {str(row.get("path")) for row in rows if row.get("path")}
    for row in _existing_confirmation_uses():
        if str(row.get("path")) not in seen:
            rows.append(row)
    return rows


def assert_holdout_unused() -> None:
    uses = holdout_uses()
    if uses:
        raise ValueError(
            "holdout has already been consumed; confirmation is not an independent test. "
            f"uses={len(uses)} log={holdout_use_log()}"
        )


def record_holdout_use(
    *,
    expression: str,
    campaign_id: str = "",
    data_hash: str = "",
    window_start: str = "",
    window_end: str = "",
    freeze_path: str = "",
) -> dict[str, Any]:
    existing = holdout_uses()
    if existing:
        raise ValueError(f"holdout already consumed {len(existing)} time(s); refusing another use")
    row = {
        "schema": "factor_lab.alpha.holdout_use.v1",
        "expression": expression,
        "campaign_id": campaign_id,
        "data_hash": data_hash,
        "window_start": window_start,
        "window_end": window_end,
        "freeze_path": freeze_path,
        "created_at": config.now_iso(),
    }
    with holdout_use_log().open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    return row
