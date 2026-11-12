"""Preflight checks for opening or resuming an Alpha campaign.

The checks are deterministic and read-only. They make the campaign contract
explicit before any new label-touching evaluation: frozen policy, frozen
pre-registration, team provenance, artifact provenance, holdout state and
current statistical power.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from factor_lab.alpha.governance import find_frozen_candidate, holdout_uses, policy_snapshot
from factor_lab.alpha.power import analyze_power
from factor_lab.alpha.prereg import verify_frozen
from factor_lab.alpha.protocol_audit import audit_campaign
from factor_lab.core import config


def _campaign_dir(campaign_id: str) -> Path:
    return Path(config.LAB_ROOT) / "alpha" / "campaigns" / str(campaign_id)


def preflight(campaign_id: str) -> dict[str, Any]:
    root = _campaign_dir(campaign_id)
    issues: list[str] = []
    warnings: list[str] = []
    checks: dict[str, bool] = {}
    campaign_path = root / "campaign.json"
    if not campaign_path.exists():
        return {"schema": "factor_lab.alpha.campaign_preflight.v1", "campaign_id": campaign_id, "ok": False, "issues": [f"missing {campaign_path}"], "warnings": [], "checks": {}}
    campaign = json.loads(campaign_path.read_text(encoding="utf-8"))
    checks["campaign_exists"] = True
    team_dir = root / "team"
    checks["team_manifest"] = (team_dir / "team_manifest.json").exists()
    if not checks["team_manifest"]:
        issues.append("missing team/team_manifest.json")
    frozen = team_dir / "pre_registration.frozen.json"
    checks["frozen_pre_registration"] = frozen.exists() and bool(verify_frozen(frozen).get("ok"))
    if not checks["frozen_pre_registration"]:
        issues.append("missing or invalid team/pre_registration.frozen.json")
    audit = audit_campaign(campaign_id, write=False)
    checks["protocol_audit_ok"] = bool(audit.get("ok"))
    if not checks["protocol_audit_ok"]:
        issues.extend(audit.get("issues") or ["protocol audit failed"])
    current_policy = policy_snapshot()
    checks["policy_hash_present"] = bool(current_policy.get("policy_hash"))
    uses = holdout_uses()
    checks["holdout_unused"] = len(uses) == 0
    if uses:
        warnings.append(f"holdout already consumed {len(uses)} time(s); confirmation is not independent")
    # Artifact expressions must be in the frozen candidate set.
    frozen_expressions = set()
    if frozen.exists():
        try:
            payload = json.loads(frozen.read_text(encoding="utf-8"))
            from factor_lab.alpha.memory import canonical_expression
            for family in payload.get("families") or []:
                for candidate in family.get("candidates") or []:
                    if candidate.get("expression"):
                        frozen_expressions.add(canonical_expression(str(candidate["expression"])))
        except Exception:  # noqa: BLE001
            pass
    from factor_lab.alpha.memory import canonical_expression
    missing_artifacts = []
    for entry in (root / "rounds").glob("*.json"):
        row = json.loads(entry.read_text(encoding="utf-8"))
        for artifact in (row.get("research_result") or {}).get("artifacts") or []:
            manifest = artifact.get("manifest_path")
            if manifest:
                dsl = Path(manifest).parent / "factor.dsl"
                if dsl.exists() and frozen_expressions and canonical_expression(dsl.read_text(encoding="utf-8").strip()) not in frozen_expressions:
                    missing_artifacts.append(str(dsl))
    checks["artifacts_in_pre_registration"] = not missing_artifacts
    if missing_artifacts:
        issues.append(f"artifacts not in frozen pre-registration: {missing_artifacts[:3]}")
    ledger_path = Path(config.LAB_ROOT) / "alpha" / "families" / "summary.json"
    try:
        summary = json.loads(ledger_path.read_text(encoding="utf-8"))
        n_eff = int((summary.get("n_eff") or summary.get("n_unique") or 0))
        n_trials = int(summary.get("n_trials") or summary.get("n_attempts") or 0)
    except Exception:  # noqa: BLE001
        n_eff = 0
        n_trials = 0
    if n_eff and n_trials:
        power = analyze_power(n_periods=max(1, int(campaign.get("max_rounds") or 1)), n_trials=n_trials, n_eff=n_eff)
        warnings.append(f"power rank1 |t| {power.get('rank1_z'):.2f} for current ledger; plan sample size explicitly")
    return {
        "schema": "factor_lab.alpha.campaign_preflight.v1",
        "campaign_id": campaign_id,
        "ok": all(checks.values()),
        "checks": checks,
        "issues": issues,
        "warnings": warnings,
        "policy": current_policy,
        "holdout_uses": len(uses),
        "pre_registration_hash": (json.loads(frozen.read_text(encoding="utf-8")).get("pre_registration_hash") if frozen.exists() else None),
    }
