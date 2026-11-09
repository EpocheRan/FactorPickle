"""DSH LLM Gateway contracts for web discovery.

A DSH subagent inherits its parent composition by default and may use
``web_search`` directly; a main session may also pass raw search results into
the no-tool summarizer path.  This module builds queries, validates the LLM
result, and writes the citation-bearing discovery artifacts consumed by
PathFinder.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import config
from .discovery import DiscoveryBrief, DiscoveryLead, write_map
from .store import read_json, write_json_atomic

DIGEST_PATH = config.LAB_ROOT / "discovery" / "web_digest.json"


@dataclass
class GatewayLead:
    title: str
    mechanism: str
    sources: list[dict[str, str]]
    confidence: float = 0.5
    tags: list[str] | None = None


def seed_queries(brief: DiscoveryBrief) -> list[str]:
    """Deterministic seed queries for the DSH search tool.

    This is not the discovery loop itself; it gives the DSH Gateway a starting
    set.  The Gateway may expand/cite/dedupe from there.
    """

    question = brief.research_question.strip()
    queries = [
        f"{question} A-share factor anomaly",
        f"{question} cross-sectional return predictability",
        f"{question} China equity factor paper",
        f"{question} investable signal transaction cost",
    ]
    for dead in brief.known_dead_ends[:2]:
        queries.append(f"{question} alternative to {dead}")
    return queries[: max(1, int(brief.max_queries))]


def build_context(brief: DiscoveryBrief, search_results: list[dict[str, Any]]) -> str:
    lines = [
        f"research_question: {brief.research_question}",
        f"constraints: {brief.constraints}",
        f"known_dead_ends: {brief.known_dead_ends}",
        "raw_search_results:",
    ]
    for index, item in enumerate(search_results, start=1):
        lines.append(
            f"[{index}] title={item.get('title','')} url={item.get('url') or item.get('href','')} "
            f"date={item.get('date') or item.get('published_at','')} snippet={item.get('snippet') or item.get('content','')}"
        )
    return "\n".join(lines)


def _clean_sources(value: Any) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for item in value or []:
        if isinstance(item, str):
            out.append({"title": item, "url": item, "retrieved_at": config.now_iso()})
        elif isinstance(item, dict):
            url = str(item.get("url") or item.get("href") or "")
            title = str(item.get("title") or url)
            if url:
                out.append(
                    {
                        "title": title,
                        "url": url,
                        "retrieved_at": str(item.get("retrieved_at") or config.now_iso()),
                    }
                )
    return out


def parse_gateway_output(payload: dict[str, Any], brief: DiscoveryBrief) -> dict[str, Any]:
    leads: list[DiscoveryLead] = []
    for raw in payload.get("leads") or []:
        if not isinstance(raw, dict):
            continue
        sources = _clean_sources(raw.get("sources"))
        leads.append(
            DiscoveryLead(
                title=str(raw.get("title") or ""),
                mechanism=str(raw.get("mechanism") or ""),
                sources=[item["url"] for item in sources],
                confidence=float(raw.get("confidence", 0.5) or 0.5),
                tags=[str(item) for item in raw.get("tags", []) or []],
            )
        )
    digest = {
        "schema_version": "factor_lab.web_digest.v1",
        "research_question": brief.research_question,
        "summary": str(payload.get("summary") or ""),
        "gaps": [str(item) for item in payload.get("gaps", []) or []],
        "leads": [
            {
                "title": lead.title,
                "mechanism": lead.mechanism,
                "sources": lead.sources,
                "confidence": lead.confidence,
                "tags": lead.tags,
            }
            for lead in leads
        ],
        "created_at": config.now_iso(),
    }
    write_json_atomic(DIGEST_PATH, digest)
    write_map(
        brief.research_question,
        leads,
        notes=str(payload.get("summary") or ""),
    )
    return digest


def read_brief(path: Path | None = None) -> DiscoveryBrief:
    raw = read_json(path or (config.LAB_ROOT / "discovery" / "discovery_brief.json"))
    if not isinstance(raw, dict):
        raise FileNotFoundError("discovery_brief.json not found")
    return DiscoveryBrief(
        research_question=str(raw.get("research_question") or ""),
        constraints=[str(item) for item in raw.get("constraints", []) or []],
        known_dead_ends=[str(item) for item in raw.get("known_dead_ends", []) or []],
        max_queries=int(raw.get("max_queries", 8) or 8),
    )
