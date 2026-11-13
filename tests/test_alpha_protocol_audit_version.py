"""Protocol-audit library scans all immutable factor versions."""

from __future__ import annotations

import json

from factor_lab.alpha import protocol_audit


def test_factor_manifests_scans_v2_and_later(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(protocol_audit.config, "LAB_ROOT", tmp_path)
    root = tmp_path / "factors" / "generated" / "fam-test" / "factor-test"
    for version, campaign in (("v1", "CAMP-OTHER"), ("v2", "CAMP-TEST")):
        version_dir = root / version
        version_dir.mkdir(parents=True)
        (version_dir / "manifest.json").write_text(
            json.dumps(
                {
                    "schema": "factor_lab.factor_artifact.v1",
                    "factor_id": "factor-test",
                    "family": "fam-test",
                    "version": version,
                    "spec": {"source_campaign": campaign},
                }
            ),
            encoding="utf-8",
        )
    rows = protocol_audit._factor_manifests("CAMP-TEST")
    assert [row["version"] for row in rows] == ["v2"]
    assert rows[0]["factor_id"] == "factor-test"


def _full_protocol_fixture(tmp_path, monkeypatch, *, task_prefix="task"):
    from factor_lab.alpha import protocol_audit
    from factor_lab.core import config

    monkeypatch.setattr(protocol_audit.config, "LAB_ROOT", tmp_path)
    monkeypatch.setattr(config, "LAB_ROOT", tmp_path)
    cid = "CAMP-FULL"
    team = tmp_path / "alpha" / "campaigns" / cid / "team"
    team.mkdir(parents=True)
    campaign = {"campaign_id": cid, "full_team_protocol": True, "protocol_version": "alpha-campaign-v2"}
    (tmp_path / "alpha" / "campaigns" / cid / "campaign.json").write_text(__import__("json").dumps(campaign), encoding="utf-8")
    roles = []
    for i, name in enumerate(("web-gateway", "pathfinder", "ml-planner", "factor-builder")):
        outputs = [str(team / f"{name}.json")]
        (team / f"{name}.json").write_text("{}", encoding="utf-8")
        if name == "web-gateway":
            outputs = [str(team / "web_gateway_map.json")]
            (team / "web_gateway_map.json").write_text("{}", encoding="utf-8")
        roles.append({"role": name, "status": "completed", "task_id": f"{task_prefix}-{i}", "outputs": outputs})
    team_manifest = {"schema": "factor_lab.alpha.team_manifest.v1", "campaign_id": cid, "protocol_version": "alpha-campaign-v2", "strict": True, "roles": roles}
    (team / "team_manifest.json").write_text(__import__("json").dumps(team_manifest), encoding="utf-8")
    return cid, campaign, team_manifest, roles


def test_campaign_create_defaults_to_full_team_protocol(tmp_path) -> None:
    from factor_lab.alpha.campaign import CampaignController

    record = CampaignController(tmp_path / "campaigns").create("fresh", max_rounds=1)
    assert record["full_team_protocol"] is True
    assert record["protocol_requirements"]["pathfinder_rejudgment"] is True


def test_full_protocol_accepts_fresh_tasks_and_campaign_scoped_outputs(tmp_path, monkeypatch) -> None:
    from factor_lab.alpha import protocol_audit

    cid, campaign, team, roles = _full_protocol_fixture(tmp_path, monkeypatch)
    role_by_name = {role["role"]: role for role in roles}
    assert protocol_audit._full_protocol_issues(cid, campaign, team, roles, role_by_name) == []


def test_full_protocol_rejects_reused_task_ids(tmp_path, monkeypatch) -> None:
    from factor_lab.alpha import protocol_audit

    cid, campaign, team, roles = _full_protocol_fixture(tmp_path, monkeypatch, task_prefix="shared")
    other = tmp_path / "alpha" / "campaigns" / "CAMP-OTHER" / "team"
    other.mkdir(parents=True)
    (other / "team_manifest.json").write_text(__import__("json").dumps({"schema": "factor_lab.alpha.team_manifest.v1", "campaign_id": "CAMP-OTHER", "roles": [{"role": "pathfinder", "status": "completed", "task_id": "shared-1"}]}), encoding="utf-8")
    role_by_name = {role["role"]: role for role in roles}
    issues = protocol_audit._full_protocol_issues(cid, campaign, team, roles, role_by_name)
    assert any("reused" in issue for issue in issues)


def test_pipeline_refuses_full_protocol_campaign_without_team(tmp_path, monkeypatch) -> None:
    import pytest
    from factor_lab.alpha.campaign import CampaignController, CampaignError
    from factor_lab.alpha.pipeline import _enforce_full_team_protocol
    from factor_lab.core import config

    monkeypatch.setattr(config, "LAB_ROOT", tmp_path / "output")
    controller = CampaignController(tmp_path / "output" / "alpha" / "campaigns")
    campaign = controller.create("full", max_rounds=1)
    with pytest.raises(CampaignError, match="full-team protocol audit failed"):
        _enforce_full_team_protocol(campaign["campaign_id"])
