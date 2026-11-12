"""Discovery contracts / provenance / novelty (P3)."""

from __future__ import annotations

from factor_lab.alpha.discovery import (
    attach_provenance,
    compile_candidates,
    novelty_check,
    validate_discovery_brief,
    validate_discovery_map,
)


def _map() -> dict:
    return {
        "schema": "factor_lab.alpha.discovery_map.v1",
        "research_question": "low price premium",
        "leads": [
            {
                "title": "A-share low price premium",
                "mechanism": "low nominal price -> higher forward return",
                "expected_sign": "positive",
                "sources": [{"url": "https://example.com/paper", "retrieved_at": "2026-11-12T00:00:00Z"}],
                "confidence": 0.7,
                "tags": ["low-price"],
            }
        ],
        "sources": [{"url": "https://example.com/paper", "retrieved_at": "2026-11-12T00:00:00Z"}],
        "retrieved_at": "2026-11-12T00:00:00Z",
    }


def _brief(lead_id: str) -> dict:
    return {
        "schema": "factor_lab.alpha.discovery_brief.v1",
        "research_question": "low price premium",
        "constraints": ["pool200", "horizon 5"],
        "known_dead_ends": ["MAX_60 alone"],
        "candidate_mechanisms": [
            {
                "mechanism": "low nominal price",
                "expected_sign": "positive",
                "fields_hint": ["low"],
                "lead_ids": [lead_id],
                "kill_criteria": ["coverage < 0.90"],
            }
        ],
    }


def test_map_and_brief_validate_and_compile_with_provenance() -> None:
    checked = validate_discovery_map(_map())
    assert checked["ok"] is True
    lead_id = checked["map"]["leads"][0]["lead_id"]
    brief = _brief(lead_id)
    assert validate_discovery_brief(brief)["ok"] is True
    plans = compile_candidates(brief, _map())
    assert plans["n_candidates"] == 1
    plan = plans["candidates"][0]
    assert plan["lead_ids"] == [lead_id]
    spec = attach_provenance({"factor_id": "x"}, plan)
    assert spec["provenance"]["lead_ids"] == [lead_id]
    assert spec["provenance"]["sources"][0]["url"] == "https://example.com/paper"


def test_map_rejects_uncited_lead_and_raw_html() -> None:
    payload = _map()
    payload["leads"][0]["sources"] = []
    payload["sources"] = []
    assert validate_discovery_map(payload)["ok"] is False
    payload = _map()
    payload["leads"][0]["mechanism"] = "<script>alert(1)</script>"
    assert validate_discovery_map(payload)["ok"] is False


def test_structural_novelty_against_empty_library(tmp_path) -> None:
    result = novelty_check("rank(neg(low))", library_root=tmp_path)
    assert result["schema"] == "factor_lab.alpha.novelty_check.v1"
    assert result["passed_novelty"] is True
    assert result["max_structural_similarity"] == 0.0
