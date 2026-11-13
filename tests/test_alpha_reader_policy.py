"""Reader mandatory-trigger policy and campaign evidence schema."""

from __future__ import annotations

import json
from pathlib import Path

from factor_lab.alpha.campaign import CampaignController
from factor_lab.alpha.reader_policy import (
    READER_EVIDENCE_SCHEMA,
    is_after_effective_from,
    load_reader_policy,
    reader_requirement,
    validate_reader_evidence,
)
from factor_lab.alpha import protocol_audit
from factor_lab.core import config


def _map(n_sources: int, **extra) -> dict:
    payload = {"schema_version": "factor_lab.discovery_map.v1", "sources": [f"https://example.com/{index}" for index in range(n_sources)]}
    payload.update(extra)
    return payload


def test_reader_required_at_source_threshold() -> None:
    policy = load_reader_policy()
    assert policy["required_when"]["web_gateway_sources_gte"] == 4
    low = reader_requirement(_map(2), policy)
    high = reader_requirement(_map(4), policy)
    assert low["required"] is False
    assert high["required"] is True
    assert high["source_count"] == 4


def test_reader_required_by_explicit_flag_or_unread_full_text() -> None:
    policy = load_reader_policy()
    explicit = reader_requirement(_map(1, reader_required=True), policy)
    assert explicit["required"] is True
    unread = reader_requirement(
        _map(
            1,
            sources=[{"url": "https://example.com/paper", "access_status": "paywalled"}],
        ),
        policy,
    )
    assert unread["required"] is True
    assert unread["unread_full_text_sources"]


def test_reader_evidence_schema_validation(tmp_path) -> None:
    path = tmp_path / "reader_evidence.json"
    path.write_text(
        json.dumps(
            {
                "schema": READER_EVIDENCE_SCHEMA,
                "campaign_id": "CAMP-TEST",
                "documents": [
                    {
                        "source_url": "https://example.com/paper",
                        "publish_at": None,
                        "events": [],
                        "evidence": [{"page": "1", "quote": "publish_at is missing"}],
                    }
                ],
                "n_sources": 1,
                "gaps": ["publish_at missing"],
            }
        ),
        encoding="utf-8",
    )
    check = validate_reader_evidence(path, campaign_id="CAMP-TEST")
    assert check["ok"] is True
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"schema": READER_EVIDENCE_SCHEMA, "documents": []}), encoding="utf-8")
    assert validate_reader_evidence(bad)["ok"] is False


def test_reader_policy_effective_from_is_deterministic() -> None:
    decision = reader_requirement(_map(4))
    assert is_after_effective_from("2026-11-13T11:30:00+08:00", decision) is True
    assert is_after_effective_from("2026-11-13T10:00:00+08:00", decision) is False


def test_audit_issues_when_post_policy_campaign_misses_required_reader(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(config, "LAB_ROOT", tmp_path / "output")
    controller = CampaignController(Path(config.LAB_ROOT) / "alpha" / "campaigns")
    campaign = controller.create("reader trigger", max_rounds=2)
    campaign_id = campaign["campaign_id"]
    campaign_path = Path(config.LAB_ROOT) / "alpha" / "campaigns" / campaign_id / "campaign.json"
    payload = json.loads(campaign_path.read_text(encoding="utf-8"))
    payload["created_at"] = "2026-11-13T12:00:00+08:00"
    campaign_path.write_text(json.dumps(payload), encoding="utf-8")
    team = Path(config.LAB_ROOT) / "alpha" / "campaigns" / campaign_id / "team"
    team.mkdir(parents=True, exist_ok=True)
    (team / "team_manifest.json").write_text(
        json.dumps(
            {
                "schema": "factor_lab.alpha.team_manifest.v1",
                "campaign_id": campaign_id,
                "protocol_version": "alpha-campaign-v2",
                "strict": True,
                "roles": [
                    {"role": "web-gateway", "status": "completed", "task_id": "task-1"},
                    {"role": "pathfinder", "status": "completed", "task_id": "task-2"},
                    {"role": "ml-planner", "status": "completed", "task_id": "task-3", "ml_skip_reason": "skip"},
                    {"role": "factor-builder", "status": "completed", "task_id": "task-4"},
                ],
            }
        ),
        encoding="utf-8",
    )
    discovery = Path(config.LAB_ROOT) / "discovery"
    discovery.mkdir(parents=True, exist_ok=True)
    (discovery / f"discovery_map_{campaign_id}.json").write_text(
        json.dumps({"schema_version": "factor_lab.discovery_map.v1", "sources": [f"https://example.com/{i}" for i in range(4)]}),
        encoding="utf-8",
    )
    audit = protocol_audit.audit_campaign(campaign_id)
    assert any("Reader is required by policy" in issue for issue in audit["issues"])
