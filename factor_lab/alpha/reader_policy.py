"""Deterministic Reader-trigger policy for Alpha campaigns.

The policy lives in ``team/roles.json`` under
``campaign_protocol.reader_policy``.  Reader remains optional when the Web
Gateway map is small; it becomes required when external evidence crosses the
configured source/full-text thresholds.  This module only decides and validates
provenance; it never generates research candidates.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


READER_EVIDENCE_SCHEMA = "factor_lab.alpha.reader_evidence.v1"
READER_DECISION_SCHEMA = "factor_lab.alpha.reader_requirement.v1"


def roles_path() -> Path:
    return Path(__file__).resolve().parent / "team" / "roles.json"


def load_reader_policy(path: str | Path | None = None) -> dict[str, Any]:
    target = Path(path) if path is not None else roles_path()
    payload = json.loads(target.read_text(encoding="utf-8"))
    policy = ((payload.get("campaign_protocol") or {}).get("reader_policy") or {})
    if not isinstance(policy, dict):
        return {}
    return policy


def _as_timestamp(value: Any) -> datetime | None:
    if value is None:
        return None
    try:
        text = str(value).strip()
        if not text:
            return None
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except Exception:  # noqa: BLE001
        return None


def _source_rows(discovery_map: dict[str, Any]) -> list[Any]:
    sources = discovery_map.get("sources")
    if isinstance(sources, list):
        return sources
    if isinstance(sources, dict):
        return list(sources.values())
    return []


def _unread_full_text(discovery_map: dict[str, Any], sources: list[Any]) -> list[str]:
    unread: list[str] = []
    for key in ("unread_full_text_sources", "paywalled_sources", "unread_sources"):
        value = discovery_map.get(key)
        if isinstance(value, list):
            unread.extend(str(item) for item in value if str(item).strip())
    for source in sources:
        if not isinstance(source, dict):
            continue
        status = str(source.get("access_status") or source.get("status") or "").strip().lower()
        if source.get("paywalled") is True or source.get("full_text_read") is False or status in {"paywalled", "unread", "blocked", "inaccessible"}:
            label = str(source.get("url") or source.get("title") or source.get("source_url") or "unknown")
            unread.append(label)
    return sorted(set(unread))


def reader_requirement(
    discovery_map: dict[str, Any] | None,
    policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a deterministic Reader-required decision for one discovery map."""
    policy = policy if policy is not None else load_reader_policy()
    required_when = policy.get("required_when") or {}
    sources = _source_rows(discovery_map or {})
    source_count = len(sources)
    threshold = int(required_when.get("web_gateway_sources_gte", 4) or 4)
    explicit = bool((discovery_map or {}).get("reader_required"))
    unread = _unread_full_text(discovery_map or {}, sources)
    reasons: list[str] = []
    if source_count >= threshold:
        reasons.append(f"web_gateway_sources={source_count} >= {threshold}")
    if explicit and required_when.get("discovery_map_reader_required", True):
        reasons.append("discovery_map.reader_required=true")
    if unread and required_when.get("unread_full_text_sources_nonempty", True):
        reasons.append(f"unread_full_text_sources={len(unread)}")
    return {
        "schema": READER_DECISION_SCHEMA,
        "required": bool(reasons),
        "reasons": reasons,
        "source_count": source_count,
        "threshold": threshold,
        "unread_full_text_sources": unread,
        "effective_from": policy.get("effective_from"),
        "output": policy.get("output") or "team/reader_evidence.json",
        "evidence_schema": policy.get("evidence_schema") or READER_EVIDENCE_SCHEMA,
    }


def is_after_effective_from(campaign_created_at: Any, decision: dict[str, Any]) -> bool:
    created = _as_timestamp(campaign_created_at)
    effective = _as_timestamp(decision.get("effective_from"))
    if created is None or effective is None:
        return False
    return created >= effective


def validate_reader_evidence(
    path: str | Path,
    *,
    campaign_id: str | None = None,
    expected_sources: int | None = None,
    schema: str = READER_EVIDENCE_SCHEMA,
) -> dict[str, Any]:
    """Validate the campaign-level Reader evidence file."""
    target = Path(path)
    issues: list[str] = []
    evidence: dict[str, Any] = {"path": str(target)}
    if not target.exists():
        return {"ok": False, "issues": [f"missing Reader evidence: {target}"], "evidence": evidence}
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "issues": [f"cannot read Reader evidence: {exc}"], "evidence": evidence}
    if not isinstance(payload, dict):
        return {"ok": False, "issues": ["Reader evidence must be an object"], "evidence": evidence}
    if str(payload.get("schema") or "") != schema:
        issues.append(f"Reader evidence schema must be {schema}")
    if campaign_id and str(payload.get("campaign_id") or "") and str(payload.get("campaign_id")) != str(campaign_id):
        issues.append(f"Reader evidence campaign_id mismatch: {payload.get('campaign_id')}")
    documents = payload.get("documents")
    if not isinstance(documents, list) or not documents:
        issues.append("Reader evidence requires a non-empty documents list")
        documents = []
    for index, document in enumerate(documents):
        if not isinstance(document, dict):
            issues.append(f"documents[{index}] must be an object")
            continue
        if not (document.get("source_url") or document.get("url")):
            issues.append(f"documents[{index}] requires source_url/url")
        if not isinstance(document.get("events"), list) and not isinstance(document.get("evidence"), list):
            issues.append(f"documents[{index}] requires events or evidence list")
    n_documents = len(documents)
    if expected_sources is not None and n_documents < min(int(expected_sources), 1):
        issues.append(f"Reader evidence documents {n_documents} < expected {expected_sources}")
    evidence.update(
        {
            "schema": payload.get("schema"),
            "campaign_id": payload.get("campaign_id"),
            "n_documents": n_documents,
            "n_sources": payload.get("n_sources"),
        }
    )
    return {"ok": not issues, "issues": issues, "evidence": evidence}
