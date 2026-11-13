"""Research Atlas aggregation/render tests."""

from __future__ import annotations

import json
from pathlib import Path

from factor_lab.alpha.research_atlas import render_portfolio, write_portfolio_report
from factor_lab.alpha.research_portfolio import build_portfolio


def _seed(root: Path) -> None:
    campaign = root / "alpha" / "campaigns" / "CAMP-TEST"
    (campaign / "rounds").mkdir(parents=True)
    (campaign / "team").mkdir(parents=True)
    (campaign / "campaign.json").write_text(json.dumps({
        "schema": "factor_lab.alpha.campaign.v1",
        "campaign_id": "CAMP-TEST",
        "objective": "test research iteration",
        "state": "stopped",
        "n_rounds": 1,
        "max_rounds": 1,
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
    }), encoding="utf-8")
    (campaign / "rounds" / "round-001.json").write_text(json.dumps({
        "schema": "factor_lab.alpha.campaign_round.v1",
        "campaign_id": "CAMP-TEST",
        "round_id": "R1",
        "round_number": 1,
        "research_question": "test",
        "agents": ["pathfinder", "factor-builder"],
        "team_tasks": ["task-1", "task-2"],
        "factor_refs": [{"factor_id": "cand-test", "family": "fam-test"}],
        "data_manifest": {"start": "2022-10-01", "end": "2025-10-31", "codes": 200, "rows": 946, "fields": ["close", "volume"], "services": ["market"], "data_hash": "abc123"},
        "research_result": {"schema": "factor_lab.alpha.research_result.v1", "metrics": {"net_t": 1.2, "net_spread": 0.002, "coverage": 0.99}, "evaluation": {"verdict": "pass"}},
        "decision": "stop",
    }), encoding="utf-8")
    (campaign / "team" / "pathfinder_question.json").write_text(json.dumps({"research_question": "does test work?", "mechanism_priority": ["test mechanism"]}), encoding="utf-8")
    artifact_dir = root / "factors" / "generated" / "fam-test" / "cand-test" / "v1"
    artifact_dir.mkdir(parents=True)
    (artifact_dir / "factor.dsl").write_text("rank(close)", encoding="utf-8")
    (artifact_dir / "manifest.json").write_text(json.dumps({"factor_id": "cand-test", "family": "fam-test", "version": "v1", "spec": {"source_campaign": "CAMP-TEST", "agents": ["factor-builder"], "team_tasks": ["task-2"]}}), encoding="utf-8")
    (campaign / "team" / "team_manifest.json").write_text(json.dumps({"schema": "factor_lab.alpha.team_manifest.v1", "campaign_id": "CAMP-TEST", "strict": True, "roles": [{"role": "pathfinder", "task_id": "task-1", "status": "completed"}, {"role": "factor-builder", "task_id": "task-2", "status": "completed", "n_artifacts": 1}]}), encoding="utf-8")
    memory = root / "alpha" / "memory"
    memory.mkdir(parents=True)
    (memory / "summary.json").write_text(json.dumps({"n_attempts": 1, "n_failures": 0}), encoding="utf-8")
    (memory / "attempts.jsonl").write_text("", encoding="utf-8")
    (memory / "edge_cards.jsonl").write_text("", encoding="utf-8")


def test_build_and_render_research_atlas(tmp_path) -> None:
    _seed(tmp_path)
    portfolio = build_portfolio(tmp_path, base_dir=tmp_path / "deliverables")
    assert portfolio["schema"] == "factor_lab.alpha.research_portfolio.v1"
    assert len(portfolio["campaigns"]) == 1
    campaign = portfolio["campaigns"][0]
    assert campaign["campaign_id"] == "CAMP-TEST"
    assert "pathfinder" in campaign["agents_used"]
    assert campaign["ideas"]
    assert campaign["candidates"][0]["metrics"]["net_t"] == 1.2
    html = render_portfolio(portfolio)
    assert "Research Atlas" in html
    assert "CAMP-TEST" in html
    assert "does test work?" in html
    # app-shell structure: every view is a hash-routed section, one per question
    for view in ("overview", "evolution", "ideas", "agents", "results", "datasets", "conclusions", "method"):
        assert f'data-view="{view}"' in html
    # campaign explorer: dense rail row + detail pane, deep-linkable
    assert 'class="camp-row"' in html
    assert 'href="#/evolution/CAMP-TEST"' in html
    assert '<article class="camp-detail" data-id="CAMP-TEST"' in html
    # flat, filterable data tables exist instead of nested scroll walls
    assert 'id="ev-list"' in html
    assert 'id="idea-list"' in html
    assert 'id="cand-body"' in html
    # self-contained: no external assets or network resources
    assert "http://" not in html
    assert "https://" not in html
    assert "<script>" in html and "<style>" in html
    out = write_portfolio_report(tmp_path / "deliverables" / "research_portfolio.html", root=tmp_path)
    assert out["ok"] is True
    assert Path(out["path"]).exists()
