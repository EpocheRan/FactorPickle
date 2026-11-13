"""Structured research memory: validation ledger, context builder, compression, audit."""

from __future__ import annotations

import json
from pathlib import Path

from factor_lab.alpha.campaign import CampaignController
from factor_lab.alpha.research_memory import CONTEXT_SCHEMA, ResearchMemory


def _campaign(root: Path, campaign_id: str, factor_id: str = "iter-test") -> None:
    directory = root / campaign_id / "team"
    directory.mkdir(parents=True)
    (root / campaign_id / "campaign.json").write_text(
        json.dumps({"schema": "factor_lab.alpha.campaign.v1", "campaign_id": campaign_id, "state": "stopped"}),
        encoding="utf-8",
    )
    (directory / "result.json").write_text(
        json.dumps(
            {
                "research_result": {
                    "schema": "factor_lab.alpha.research_result.v1",
                    "factor_ref": {"factor_id": factor_id},
                    "metrics": {"coverage": 0.95, "turnover": 0.3, "net_t": 1.2},
                },
                "long_only": {"size_screened": {"long_only_t": 2.5, "long_only_net_excess_annualized": 0.06}},
                "promotion": {"verdict": "pass", "metrics": {"ir": 0.9}},
                "evaluation": {"verdict": "fail", "reasons": ["net_t below 2"]},
            }
        ),
        encoding="utf-8",
    )


def test_validation_ledger_status_and_hard_negatives(tmp_path: Path) -> None:
    memory = ResearchMemory(root=tmp_path / "memory", campaign_root=tmp_path / "campaigns")
    memory.record_validation(
        "CAMP-a",
        "iter-a",
        {"independent": {"status": "fail", "alpha_t": 0.7}, "fdr": {"status": "fail"}, "topn": {"status": "warn"}, "capacity": {"status": "unverified"}},
    )
    rows = memory.read_validations()
    assert len(rows) == 1
    assert rows[0]["status"] == "fail"
    hard = memory.hard_negatives(["CAMP-a"])
    assert len(hard) == 1
    assert hard[0]["campaign_id"] == "CAMP-a"
    assert "independent" in hard[0]["failed_tests"]


def test_context_builder_keeps_all_hard_negatives(tmp_path: Path) -> None:
    campaigns = tmp_path / "campaigns"
    _campaign(campaigns, "CAMP-a", "iter-a")
    _campaign(campaigns, "CAMP-b", "iter-b")
    memory = ResearchMemory(root=tmp_path / "memory", campaign_root=campaigns)
    memory.record_validation("CAMP-a", "iter-a", {"independent": {"status": "fail"}, "fdr": {"status": "fail"}})
    memory.record_validation("CAMP-b", "iter-b", {"independent": {"status": "pass"}, "fdr": {"status": "pass"}})
    context = memory.build_context(["CAMP-a", "CAMP-b"], budget_chars=12000)
    assert context["schema"] == CONTEXT_SCHEMA
    assert len(context["hard_constraints"]["hard_negatives"]) == 1
    assert context["source_hashes"]["CAMP-a"]
    check = memory.validate_context(context)
    assert check["ok"], check


def test_context_compression_uses_injected_compressor(tmp_path: Path) -> None:
    campaigns = tmp_path / "campaigns"
    for index in range(4):
        _campaign(campaigns, f"CAMP-{index}", f"iter-{index}")
    memory = ResearchMemory(root=tmp_path / "memory", campaign_root=campaigns)
    calls = []

    def compressor(payload):
        calls.append(payload)
        return {
            "source_campaign_ids": [row["campaign_id"] for row in payload["campaigns"]],
            "summary": "compressed",
        }

    context = memory.build_context([f"CAMP-{index}" for index in range(4)], budget_chars=200, compressor=compressor)
    assert calls
    assert context["compression"]["mode"] == "llm"
    assert context["compressed_history"]["summary"] == "compressed"


def test_context_validation_detects_stale_source_hash(tmp_path: Path) -> None:
    campaigns = tmp_path / "campaigns"
    _campaign(campaigns, "CAMP-a", "iter-a")
    memory = ResearchMemory(root=tmp_path / "memory", campaign_root=campaigns)
    context = memory.build_context(["CAMP-a"], budget_chars=12000)
    assert memory.validate_context(context)["ok"]
    result_path = campaigns / "CAMP-a" / "team" / "result.json"
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    payload["research_result"]["factor_ref"]["factor_id"] = "iter-changed"
    result_path.write_text(json.dumps(payload), encoding="utf-8")
    check = memory.validate_context(context)
    assert not check["ok"]
    assert any("hash mismatch" in issue for issue in check["issues"])


def test_campaign_create_writes_structured_context(tmp_path: Path) -> None:
    campaigns = tmp_path / "campaigns"
    _campaign(campaigns, "CAMP-parent", "iter-parent")
    memory_root = tmp_path / "memory"
    memory = ResearchMemory(root=memory_root, campaign_root=campaigns)
    memory.record_validation("CAMP-parent", "iter-parent", {"fdr": {"status": "fail"}})
    record = CampaignController(root=campaigns).create(
        "next research question",
        campaign_id="CAMP-child",
        prior_campaign_ids=["CAMP-parent"],
        memory_root=memory_root,
    )
    assert record["parent_campaign_ids"] == ["CAMP-parent"]
    context_path = campaigns / "CAMP-child" / "team" / "research_context.json"
    assert context_path.exists()
    context = json.loads(context_path.read_text(encoding="utf-8"))
    assert context["schema"] == CONTEXT_SCHEMA
    assert memory.validate_context(context, campaign_id="CAMP-child")["ok"]


def test_deepseek_compressor_parses_json_and_validates_sources(monkeypatch) -> None:
    from factor_lab.alpha.llm_compressor import CompressorError, DeepSeekCompressor

    compressor = DeepSeekCompressor(api_key="test-key", model="deepseek-flash")
    payload = {"campaigns": [{"campaign_id": "CAMP-a"}, {"campaign_id": "CAMP-b"}], "hard_negatives": []}

    def fake_post(body):
        return {"choices": [{"message": {"content": "{\"source_campaign_ids\": [\"CAMP-a\", \"CAMP-b\"], \"summary\": \"ok\"}"}}]}

    monkeypatch.setattr(compressor, "_post", fake_post)
    result = compressor(payload)
    assert result["summary"] == "ok"
    assert set(result["source_campaign_ids"]) == {"CAMP-a", "CAMP-b"}

    def missing_post(body):
        return {"choices": [{"message": {"content": "{\"source_campaign_ids\": [\"CAMP-a\"], \"summary\": \"bad\"}"}}]}

    monkeypatch.setattr(compressor, "_post", missing_post)
    try:
        compressor(payload)
    except CompressorError as exc:
        assert "omitted" in str(exc)
    else:
        raise AssertionError("expected missing source campaigns to fail")


def test_memory_audit_requires_context_after_policy(monkeypatch, tmp_path: Path) -> None:
    from factor_lab.alpha import protocol_audit

    monkeypatch.setattr(protocol_audit, "_team_dir", lambda campaign_id: tmp_path)
    monkeypatch.setattr(
        protocol_audit,
        "_memory_policy",
        lambda: {"required": True, "required_from": "2026-11-13T22:40:00+08:00", "require_data_provenance": True},
    )
    campaign = {
        "created_at": "2026-11-13T23:00:00+08:00",
        "research_memory": True,
        "parent_campaign_ids": [],
    }
    issues = protocol_audit._research_memory_issues("CAMP-new", campaign)
    assert any("missing team/research_context.json" in issue for issue in issues)


def test_context_validation_detects_data_provenance_mismatch(tmp_path: Path) -> None:
    campaigns = tmp_path / "campaigns"
    _campaign(campaigns, "CAMP-a", "iter-a")
    result_path = campaigns / "CAMP-a" / "team" / "result.json"
    result = json.loads(result_path.read_text(encoding="utf-8"))
    result["data_manifest"] = {"start": "2017-01-03", "end": "2025-10-31", "data_hash": "hash-a"}
    result_path.write_text(json.dumps(result), encoding="utf-8")
    memory = ResearchMemory(root=tmp_path / "memory", campaign_root=campaigns)
    context = memory.build_context(["CAMP-a"], budget_chars=12000)
    assert context["source_hashes"]["CAMP-a"]["data_manifest_hash"]
    assert memory.validate_context(context)["ok"]
    result["data_manifest"]["data_hash"] = "hash-b"
    result_path.write_text(json.dumps(result), encoding="utf-8")
    check = memory.validate_context(context)
    assert not check["ok"]
    assert any("provenance mismatch" in issue for issue in check["issues"])
