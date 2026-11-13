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
from datetime import datetime
from typing import Any

from factor_lab.alpha.campaign import CampaignController
from factor_lab.alpha.prereg import verify_frozen
from factor_lab.alpha.reader_policy import (
    is_after_effective_from,
    reader_requirement,
    validate_reader_evidence,
)
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


FULL_TEAM_ROLES = ("web-gateway", "pathfinder", "ml-planner", "factor-builder")


def _team_task_owners() -> dict[str, set[str]]:
    """Map task_id -> campaign ids from every recorded team manifest."""
    owners: dict[str, set[str]] = {}
    root = config.LAB_ROOT / "alpha" / "campaigns"
    if not root.exists():
        return owners
    for path in root.glob("*/team/team_manifest.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        campaign_id = str(payload.get("campaign_id") or path.parents[1].name)
        for role in payload.get("roles") or []:
            if not isinstance(role, dict):
                continue
            task_id = str(role.get("task_id") or "").strip()
            if task_id:
                owners.setdefault(task_id, set()).add(campaign_id)
    return owners


def _output_is_campaign_scoped(value: Any, campaign_id: str, team_dir: Path) -> bool:
    path = Path(str(value))
    text = str(path).replace("\\", "/")
    if campaign_id in text:
        return True
    try:
        resolved = path.resolve()
    except Exception:  # noqa: BLE001
        resolved = path
    try:
        resolved.relative_to(team_dir.resolve())
        return True
    except Exception:  # noqa: BLE001
        return False


def _full_protocol_issues(
    campaign_id: str,
    campaign: dict[str, Any],
    team: dict[str, Any] | None,
    roles: list[dict[str, Any]],
    role_by_name: dict[str, dict[str, Any]],
) -> list[str]:
    """Enforce one freshly delegated team per campaign.

    This is deliberately checked from campaign files only: the Lead cannot make
    one PathFinder/FactorBuilder decision and relabel N campaigns as N
    iterations.  A reused task id or a shared output belongs to the original
    campaign, not a new one.
    """
    if not bool(campaign.get("full_team_protocol")):
        return []
    issues: list[str] = []
    owners = _team_task_owners()
    team_dir = _team_dir(campaign_id)
    required_task_ids: dict[str, str] = {}
    for name in FULL_TEAM_ROLES:
        role = role_by_name.get(name) or {}
        if str(role.get("status") or "") != "completed":
            issues.append(f"full-team protocol: {name} role is not completed")
            continue
        task_id = str(role.get("task_id") or "").strip()
        if not task_id:
            issues.append(f"full-team protocol: {name} role has no task_id")
            continue
        required_task_ids[name] = task_id
        others = sorted(owners.get(task_id, set()) - {campaign_id})
        if others:
            issues.append(
                f"full-team protocol: {name} task {task_id} is reused by {others}; "
                "each campaign needs a fresh teammate task"
            )
        outputs = [str(item) for item in (role.get("outputs") or []) if str(item).strip()]
        if not outputs:
            if name == "ml-planner" and str(role.get("ml_skip_reason") or "").strip():
                continue
            issues.append(f"full-team protocol: {name} has no campaign-scoped output")
            continue
        for output in outputs:
            path = Path(output)
            if not path.exists() and not path.is_absolute():
                path = Path(config.LAB_ROOT).parent / path
            if not path.exists():
                issues.append(f"full-team protocol: {name} output does not exist: {output}")
                continue
            if not _output_is_campaign_scoped(output, campaign_id, team_dir):
                issues.append(
                    f"full-team protocol: {name} output is not campaign-scoped: {output}"
                )
    # A campaign is one decision, not a batch of independent campaigns.
    frozen = team_dir / "pre_registration.frozen.json"
    if frozen.exists():
        try:
            payload = json.loads(frozen.read_text(encoding="utf-8"))
            n_candidates = sum(len(family.get("candidates") or []) for family in payload.get("families") or [])
            if n_candidates > 2:
                issues.append(
                    f"full-team protocol: {n_candidates} candidates in one campaign; "
                    "max is 2, one independent campaign per research decision"
                )
        except Exception as exc:  # noqa: BLE001
            issues.append(f"full-team protocol: cannot read pre-registration: {exc}")
    # Each round must carry every fresh task id, not just a generic team label.
    rounds = CampaignController().rounds(campaign_id)
    for row in rounds:
        recorded = {str(item) for item in (row.get("team_tasks") or [])}
        missing = sorted(set(required_task_ids.values()) - recorded)
        if missing:
            issues.append(
                f"full-team protocol: round {row.get('round_id')} missing team task ids {missing}"
            )
    return issues



def _load_gateway_map(campaign_id: str, gateway_role: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    candidates = [Path(str(item)) for item in (gateway_role.get("outputs") or [])]
    candidates.append(config.LAB_ROOT / "discovery" / f"discovery_map_{campaign_id}.json")
    for path in candidates:
        if not path.exists() or not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if isinstance(payload, dict):
            return payload, str(path)
    return None, None


def _factor_manifests(campaign_id: str) -> list[dict[str, Any]]:
    rows = []
    library = config.LAB_ROOT / "factors" / "generated"
    # A factor may have several immutable versions.  Audit all of them, not
    # only v1: a re-used or diagnostics artifact can legitimately live in v2+.
    for path in glob.glob(str(library / "*" / "*" / "v*" / "manifest.json")):
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        version = str(payload.get("version") or Path(path).parent.name)
        if not version.startswith("v") or not version[1:].isdigit():
            continue
        spec = payload.get("spec") or {}
        if str(spec.get("source_campaign") or "") == campaign_id:
            rows.append({"factor_id": payload.get("factor_id"), "family": payload.get("family"), "version": version, "manifest_path": path, "spec": spec})
    return sorted(rows, key=lambda row: (str(row.get("family")), str(row.get("factor_id")), str(row.get("version"))))


def _memory_policy() -> dict[str, Any]:
    path = Path(__file__).with_name("team") / "roles.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return dict((payload.get("campaign_protocol") or {}).get("research_memory_policy") or {})
    except Exception:  # noqa: BLE001
        return {}


def _memory_required(campaign: dict[str, Any], policy: dict[str, Any]) -> bool:
    if bool(campaign.get("synthetic_fixture")):
        return False
    raw = str(policy.get("required_from") or "")
    created = str(campaign.get("created_at") or "")
    if not raw or not created:
        return bool(policy.get("required"))
    try:
        required_ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        created_ts = datetime.fromisoformat(created.replace("Z", "+00:00"))
        return bool(policy.get("required")) and created_ts >= required_ts
    except ValueError:
        return bool(policy.get("required"))


def _research_memory_issues(campaign_id: str, campaign: dict[str, Any]) -> list[str]:
    """Validate the structured research-memory context; required for new campaigns."""
    policy = _memory_policy()
    required = _memory_required(campaign, policy)
    context_path = _team_dir(campaign_id) / "research_context.json"
    marker = bool(campaign.get("research_memory")) or bool(campaign.get("parent_campaign_ids"))
    if not context_path.exists():
        if required or marker:
            return ["research_memory: missing team/research_context.json"]
        return []
    try:
        payload = json.loads(context_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return [f"research_memory: cannot read research_context.json: {exc}"]
    from factor_lab.alpha.research_memory import ResearchMemory

    issues: list[str] = []
    if required and "parent_campaign_ids" not in campaign:
        issues.append("research_memory: campaign is missing parent_campaign_ids field")
    check = ResearchMemory().validate_context(payload, campaign_id=campaign_id)
    issues.extend(str(item) for item in check.get("issues") or [])
    if required and bool(policy.get("require_data_provenance", True)):
        source_hashes = payload.get("source_hashes") or {}
        for parent, record in source_hashes.items():
            if isinstance(record, dict) and not record.get("data_manifest_hash"):
                issues.append(f"research_memory: source {parent} has no data provenance hash")
    return [f"research_memory: {item}" for item in issues]


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
    evidence["full_team_protocol"] = bool(campaign.get("full_team_protocol"))
    issues.extend(_full_protocol_issues(campaign_id, campaign, team, roles, role_by_name))

    protocol_version = str(campaign.get("protocol_version") or (team or {}).get("protocol_version") or "")
    evidence["protocol_version"] = protocol_version or "legacy"
    if protocol_version == "alpha-campaign-v2":
        gateway_role = role_by_name.get("web-gateway") or {}
        gateway_outputs = [Path(str(item)) for item in (gateway_role.get("outputs") or [])]
        gateway_fallback = config.LAB_ROOT / "discovery" / f"discovery_map_{campaign_id}.json"
        gateway_ok = any(path.exists() for path in gateway_outputs) or gateway_fallback.exists()
        if str(gateway_role.get("status") or "") != "completed":
            issues.append("alpha-campaign-v2 requires a completed web-gateway discovery role")
        elif not gateway_role.get("task_id"):
            issues.append("web-gateway role has no task_id")
        elif not gateway_ok:
            issues.append("web-gateway role has no existing discovery map output")
        ml_role = role_by_name.get("ml-planner") or {}
        ml_plan_exists = any(Path(str(item)).exists() for item in (ml_role.get("outputs") or []))
        ml_skip = str(ml_role.get("ml_skip_reason") or (team or {}).get("ml_skip_reason") or "").strip()
        if str(ml_role.get("status") or "") != "completed":
            issues.append("alpha-campaign-v2 requires a completed ml-planner decision role")
        elif not ml_role.get("task_id"):
            issues.append("ml-planner role has no task_id")
        elif not ml_plan_exists and not ml_skip:
            issues.append("ml-planner role has neither a plan output nor an explicit ml_skip_reason")
        evidence["external_evidence"] = {"gateway_outputs": [str(path) for path in gateway_outputs], "gateway_ok": gateway_ok}
        evidence["ml_decision"] = {"plan_exists": ml_plan_exists, "skip_reason": ml_skip or None}

        # Reader trigger policy: a large or explicitly-reader-required discovery
        # map must have a completed Reader task and campaign evidence file.
        discovery_map, discovery_path = _load_gateway_map(campaign_id, gateway_role)
        reader_decision = reader_requirement(discovery_map)
        reader_role = role_by_name.get("reader") or {}
        reader_path = _team_dir(campaign_id) / "reader_evidence.json"
        reader_check = validate_reader_evidence(reader_path, campaign_id=campaign_id)
        reader_effective = is_after_effective_from(campaign.get("created_at"), reader_decision)
        evidence["reader_policy"] = {
            "discovery_map": discovery_path,
            "required": bool(reader_decision.get("required")),
            "reasons": list(reader_decision.get("reasons") or []),
            "source_count": reader_decision.get("source_count"),
            "threshold": reader_decision.get("threshold"),
            "effective_from": reader_decision.get("effective_from"),
            "effective": bool(reader_effective),
            "role_status": reader_role.get("status"),
            "task_id": reader_role.get("task_id"),
            "evidence_path": str(reader_path),
            "evidence_ok": bool(reader_check.get("ok")),
            "evidence_issues": list(reader_check.get("issues") or []),
        }
        if reader_decision.get("required"):
            reasons = []
            if str(reader_role.get("status") or "") != "completed":
                reasons.append("reader role is not completed")
            elif not reader_role.get("task_id"):
                reasons.append("reader role has no task_id")
            if not reader_check.get("ok"):
                reasons.extend(reader_check.get("issues") or ["reader evidence missing/invalid"])
            if reasons:
                message = "Reader is required by policy: " + "; ".join(reasons)
                if reader_effective:
                    issues.append(message)
                else:
                    warnings.append(message + " (pre-policy campaign; historical warning)")

    artifact_rows = _factor_manifests(campaign_id)
    evidence["artifacts"] = [{"factor_id": row["factor_id"], "family": row["family"], "version": row["version"], "manifest_path": row["manifest_path"]} for row in artifact_rows]
    ml_artifact_ids = {str(item.get("factor_id")) for item in ((team or {}).get("ml_artifacts") or []) if isinstance(item, dict)}
    # Verify each artifact expression is actually in the frozen pre-registration.
    frozen_path_for_check = _team_dir(campaign_id) / "pre_registration.frozen.json"
    expressed = set()
    if frozen_path_for_check.exists():
        try:
            frozen_payload = json.loads(frozen_path_for_check.read_text(encoding="utf-8"))
            from factor_lab.alpha.memory import canonical_expression
            for family in frozen_payload.get("families") or []:
                for candidate in family.get("candidates") or []:
                    expressed.add(canonical_expression(str(candidate.get("expression") or "")))
        except Exception:  # noqa: BLE001
            expressed = set()
    if expressed:
        from factor_lab.alpha.memory import canonical_expression
        diagnostic_ids = {str(item) for item in ((team or {}).get("diagnostic_artifacts") or [])}
        for row in artifact_rows:
            if str(row.get("factor_id")) in diagnostic_ids or str(row.get("factor_id")) in ml_artifact_ids:
                continue
            dsl_path = Path(row["manifest_path"]).parent / "factor.dsl"
            if dsl_path.exists():
                expression = canonical_expression(dsl_path.read_text(encoding="utf-8").strip())
                if expression not in expressed:
                    issues.append(f"artifact not in frozen pre-registration: {row['factor_id']}")
    evidence["n_artifacts"] = len(artifact_rows)
    evidence["ml_artifacts"] = sorted(ml_artifact_ids)
    if not artifact_rows:
        issues.append("no FactorBuilder artifacts carry source_campaign")
    factor_role = role_by_name.get("factor-builder") or {}
    deterministic_artifacts = [row for row in artifact_rows if str(row.get("factor_id")) not in ml_artifact_ids]
    if factor_role and int(factor_role.get("n_artifacts") or 0) != len(deterministic_artifacts):
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

    # First-class evaluation-universe provenance.  Historical rounds may omit it;
    # for new v2 campaigns a missing field is a warning, not a data-validity
    # failure, because the full-panel default is still explicit on the round.
    eval_rounds_all = [
        row for row in rounds
        if str((row.get("research_result") or {}).get("schema") or "") == "factor_lab.alpha.research_result.v1"
    ]
    universe_rounds = [row for row in rounds if isinstance(row.get("evaluation_universe"), dict)]
    evidence["evaluation_universe"] = [
        {
            "round_id": row.get("round_id"),
            "schema": (row.get("evaluation_universe") or {}).get("schema"),
            "kind": (row.get("evaluation_universe") or {}).get("kind"),
            "eligible_fraction": ((row.get("evaluation_universe") or {}).get("universe") or {}).get("eligible_fraction"),
        }
        for row in universe_rounds
    ]
    if protocol_version == "alpha-campaign-v2" and eval_rounds_all and not universe_rounds:
        warnings.append("v2 evaluation rounds lack first-class evaluation_universe records")
    for row in universe_rounds:
        record = row.get("evaluation_universe") or {}
        if str(record.get("schema") or "") != "factor_lab.alpha.evaluation_universe.v1":
            issues.append(f"{row.get('round_id')}: evaluation_universe schema is invalid")
            break

    from factor_lab.alpha.evaluation_contract import validate_evaluation_contract

    contract_rounds = [row for row in rounds if isinstance(row.get("evaluation_contract"), dict)]
    contract_issues = []
    for row in contract_rounds:
        check = validate_evaluation_contract(row.get("evaluation_contract"))
        if not check.get("ok"):
            contract_issues.append({"round_id": row.get("round_id"), "issues": check.get("issues")})
    evidence["evaluation_contract"] = [
        {
            "round_id": row.get("round_id"),
            "schema": (row.get("evaluation_contract") or {}).get("schema"),
            "evidence_kind": (row.get("evaluation_contract") or {}).get("evidence_kind"),
            "evaluation_start": (row.get("evaluation_contract") or {}).get("evaluation_start"),
            "evaluation_end": (row.get("evaluation_contract") or {}).get("evaluation_end"),
            "n_eff": (row.get("evaluation_contract") or {}).get("n_eff"),
            "n_trials": (row.get("evaluation_contract") or {}).get("n_trials"),
        }
        for row in contract_rounds
    ]
    if contract_issues:
        issues.extend(f"{item['round_id']}: evaluation contract invalid: {item['issues']}" for item in contract_issues)
    if protocol_version == "alpha-campaign-v2" and eval_rounds_all and not contract_rounds:
        warnings.append("v2 evaluation rounds lack first-class evaluation_contract records (historical rounds)")

    # Pre-registration round must appear before the first evaluation result.
    eval_rounds = [row for row in rounds if str((row.get("research_result") or {}).get("schema") or "") == "factor_lab.alpha.research_result.v1"]
    prereg_rounds = [row for row in rounds if "pre_registration" in str((row.get("research_result") or {}).get("schema") or "")]
    if prereg_rounds and eval_rounds:
        if min(row["round_number"] for row in prereg_rounds) >= min(row["round_number"] for row in eval_rounds):
            issues.append("pre-registration round is not before the first evaluation round")
    evidence["pre_registration_rounds"] = [row.get("round_id") for row in prereg_rounds]
    evidence["evaluation_rounds"] = [row.get("round_id") for row in eval_rounds]

    memory_issues = _research_memory_issues(campaign_id, campaign)
    if memory_issues:
        issues.extend(memory_issues)
    evidence["research_memory"] = {
        "enabled": bool(campaign.get("research_memory") or campaign.get("parent_campaign_ids")),
        "parent_campaign_ids": list(campaign.get("parent_campaign_ids") or []),
        "issues": memory_issues,
    }

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
