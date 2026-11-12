"""Campaign protocol audit for the Alpha engine.

The audit is intentionally deterministic: it reads campaign files, the team
manifest, the frozen pre-registration and Factor Library manifests.  It does
not call an LLM and it cannot promote a factor.  Its job is to make the
campaign protocol (PathFinder -> frozen pre-registration -> FactorBuilder
artifacts -> deterministic evaluation) visible and enforceable.
"""

from __future__ import annotations

import glob
import json
from pathlib import Path
from typing import Any

from factor_lab.alpha.campaign import CampaignController
from factor_lab.alpha.prereg import verify_frozen
from factor_lab.core import config
from factor_lab.core.store import write_json_atomic


AUDIT_SCHEMA = "factor_lab.alpha.campaign_protocol_audit.v1"
TEAM_MANIFEST_SCHEMA = "factor_lab.alpha.team_manifest.v1"


def _team_dir(campaign_id: str) -> Path:
    return config.LAB_ROOT / "alpha" / "campaigns" / campaign_id / "team"


def _load_team_manifest(campaign_id: str) -> tuple[dict[str, Any] | None, list[str]]:
    path = _team_dir(campaign_id) / "team_manifest.json"
    if not path.exists():
        return None, [f"missing team manifest: {path}"]
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return None, [f"cannot read team manifest: {exc}"]
    if payload.get("schema") != TEAM_MANIFEST_SCHEMA:
        return payload, [f"team manifest schema must be {TEAM_MANIFEST_SCHEMA}"]
    if str(payload.get("campaign_id") or "") != campaign_id:
        return payload, [f"team manifest campaign_id mismatch: {payload.get('campaign_id')}"]
    return payload, []


def _factor_manifests(campaign_id: str) -> list[dict[str, Any]]:
    rows = []
    library = config.LAB_ROOT / "factors" / "generated"
    for path in glob.glob(str(library / "*" / "*" / "v1" / "manifest.json")):
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        spec = payload.get("spec") or {}
        if str(spec.get("source_campaign") or "") == campaign_id:
            rows.append({"factor_id": payload.get("factor_id"), "family": payload.get("family"), "version": payload.get("version"), "manifest_path": path, "spec": spec})
    return sorted(rows, key=lambda row: (str(row.get("family")), str(row.get("factor_id"))))


def audit_campaign(campaign_id: str, *, write: bool = False) -> dict[str, Any]:
    issues: list[str] = []
    warnings: list[str] = []
    evidence: dict[str, Any] = {"campaign_id": campaign_id}
    try:
        campaign = CampaignController().load(campaign_id)
        rounds = CampaignController().rounds(campaign_id)
    except Exception as exc:  # noqa: BLE001
        return {"schema": AUDIT_SCHEMA, "campaign_id": campaign_id, "ok": False, "issues": [f"campaign not found: {exc}"], "warnings": [], "evidence": evidence}
    evidence["state"] = campaign.get("state")
    evidence["n_rounds"] = campaign.get("n_rounds")
    evidence["max_rounds"] = campaign.get("max_rounds")

    team, team_issues = _load_team_manifest(campaign_id)
    issues.extend(team_issues)
    roles = list((team or {}).get("roles") or [])
    role_by_name = {str(role.get("role") or ""): role for role in roles}
    for required in ("pathfinder", "factor-builder"):
        role = role_by_name.get(required)
        if role is None:
            issues.append(f"team manifest missing required role: {required}")
        elif str(role.get("status") or "") != "completed":
            issues.append(f"{required} task is not completed: {role.get('status')}")
        elif not role.get("task_id"):
            issues.append(f"{required} task has no task_id")
    evidence["roles"] = roles

    artifact_rows = _factor_manifests(campaign_id)
    evidence["artifacts"] = [{"factor_id": row["factor_id"], "family": row["family"], "version": row["version"], "manifest_path": row["manifest_path"]} for row in artifact_rows]
    evidence["n_artifacts"] = len(artifact_rows)
    if not artifact_rows:
        issues.append("no FactorBuilder artifacts carry source_campaign")
    factor_role = role_by_name.get("factor-builder") or {}
    if factor_role and int(factor_role.get("n_artifacts") or 0) != len(artifact_rows):
        warnings.append(f"team manifest n_artifacts={factor_role.get('n_artifacts')} but library has {len(artifact_rows)}")

    # Pre-registration: frozen file must exist and be valid before evaluation.
    frozen_path = _team_dir(campaign_id) / "pre_registration.frozen.json"
    evidence["pre_registration_frozen"] = str(frozen_path)
    if not frozen_path.exists():
        warnings.append("no frozen pre-registration file (campaign may be exploratory, not pre-registered)")
    else:
        try:
            check = verify_frozen(frozen_path)
            evidence["pre_registration_hash"] = check.get("hash")
            if not check.get("ok"):
                issues.append("frozen pre-registration hash/schema verification failed")
        except Exception as exc:  # noqa: BLE001
            issues.append(f"cannot verify frozen pre-registration: {exc}")

    # All evaluation rounds should carry team provenance. Historical campaigns
    # may lack it; this is a warning rather than a data-validity failure.
    missing_provenance = []
    for row in rounds:
        schema = str((row.get("research_result") or {}).get("schema") or "")
        if schema in {"factor_lab.alpha.pre_registration_round.v1", "factor_lab.alpha.p0p1_artifacts_round.v1"}:
            continue
        if not row.get("team_tasks") and not row.get("agents"):
            missing_provenance.append(row.get("round_id"))
    if missing_provenance:
        message = f"{len(missing_provenance)} evaluation rounds lack agents/team_tasks: {missing_provenance[:5]}"
        if bool((team or {}).get("strict", False)):
            issues.append(message)
        else:
            warnings.append(message)
    evidence["round_provenance"] = {
        "rounds_with_provenance": sum(1 for row in rounds if row.get("team_tasks") or row.get("agents")),
        "rounds_without_provenance": missing_provenance,
    }

    # Pre-registration round must appear before the first evaluation result.
    eval_rounds = [row for row in rounds if str((row.get("research_result") or {}).get("schema") or "") == "factor_lab.alpha.research_result.v1"]
    prereg_rounds = [row for row in rounds if "pre_registration" in str((row.get("research_result") or {}).get("schema") or "")]
    if prereg_rounds and eval_rounds:
        if min(row["round_number"] for row in prereg_rounds) >= min(row["round_number"] for row in eval_rounds):
            issues.append("pre-registration round is not before the first evaluation round")
    evidence["pre_registration_rounds"] = [row.get("round_id") for row in prereg_rounds]
    evidence["evaluation_rounds"] = [row.get("round_id") for row in eval_rounds]

    payload = {
        "schema": AUDIT_SCHEMA,
        "campaign_id": campaign_id,
        "ok": not issues,
        "issues": issues,
        "warnings": warnings,
        "evidence": evidence,
        "created_at": config.now_iso(),
    }
    if write:
        target = _team_dir(campaign_id) / "PROTOCOL_AUDIT.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        payload["path"] = str(target)
        write_json_atomic(target, payload)
    return payload
