"""Web Gateway artifacts.

The Gateway itself is an LLM role.  This module owns the contracts and a local,
deterministic fallback for tests/offline development; it does not pretend to
be a web search implementation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from factor_lab.core import config
from factor_lab.core.store import read_json, write_json_atomic

DISCOVERY_DIR = config.LAB_ROOT / "discovery"


@dataclass
class DiscoveryBrief:
    research_question: str
    constraints: list[str] = field(default_factory=list)
    known_dead_ends: list[str] = field(default_factory=list)
    max_queries: int = 8

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "factor_lab.discovery_brief.v1",
            "research_question": self.research_question,
            "constraints": list(self.constraints),
            "known_dead_ends": list(self.known_dead_ends),
            "max_queries": self.max_queries,
            "created_at": config.now_iso(),
        }


@dataclass
class DiscoveryLead:
    title: str
    mechanism: str
    sources: list[str] = field(default_factory=list)
    confidence: float = 0.5
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "mechanism": self.mechanism,
            "sources": list(self.sources),
            "confidence": float(self.confidence),
            "tags": list(self.tags),
        }


def write_brief(brief: DiscoveryBrief, path: Path | None = None) -> Path:
    target = path or (DISCOVERY_DIR / "discovery_brief.json")
    write_json_atomic(target, brief.to_dict())
    return target


def write_map(
    question: str,
    leads: list[DiscoveryLead],
    *,
    path: Path | None = None,
    notes: str = "",
) -> Path:
    target = path or (DISCOVERY_DIR / "discovery_map.json")
    sources = sorted({source for lead in leads for source in lead.sources})
    write_json_atomic(
        target,
        {
            "schema_version": "factor_lab.discovery_map.v1",
            "research_question": question,
            "leads": [lead.to_dict() for lead in leads],
            "sources": sources,
            "notes": notes,
            "created_at": config.now_iso(),
        },
    )
    return target


def read_map(path: Path | None = None) -> dict[str, Any] | None:
    raw = read_json(path or (DISCOVERY_DIR / "discovery_map.json"))
    return raw if isinstance(raw, dict) else None


class LocalDiscoveryGateway:
    """Offline contract implementation; replaceable by the DSH LLM gateway."""

    def __init__(self, leads: list[DiscoveryLead] | None = None):
        self.leads = list(leads or [])

    def discover(self, brief: DiscoveryBrief) -> list[DiscoveryLead]:
        dead = " ".join(brief.known_dead_ends).lower()
        result = []
        for lead in self.leads:
            category = " ".join([lead.title, lead.mechanism, *lead.tags]).lower()
            if dead and category in dead:
                continue
            result.append(lead)
        return result[: max(1, brief.max_queries)]
