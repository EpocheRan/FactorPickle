from __future__ import annotations

from factor_lab.team import load_roles, validate_roles
from factor_lab.web_gateway import seed_queries
from factor_lab.discovery import DiscoveryBrief
from factor_lab import config


def test_team_roles_are_portable() -> None:
    validation = validate_roles()
    assert validation["ok"]
    assert validation["count"] == 8
    assert set(validation["names"]) == {
        "pathfinder",
        "web-gateway",
        "factor-builder",
        "runner",
        "evaluator",
        "ml-planner",
        "ml-monitor",
        "supervisor",
    }
    assert all("prompt" in role for role in load_roles())


def test_source_catalog_is_tracked_location() -> None:
    path = config.PROJECT_ROOT / "factor_lab" / "sources" / "catalog.json"
    assert path.exists()
    assert path.stat().st_size > 1000


def test_seed_queries_prioritize_catalog_domains() -> None:
    queries = seed_queries(DiscoveryBrief(research_question="52 week high factor", max_queries=8))
    assert any(query.startswith("site:") for query in queries)
    assert queries[0].startswith("site:")
