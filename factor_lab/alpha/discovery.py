"""Web Gateway discovery contract for Alpha research memory.

This module does not perform network calls.  A Gateway/LLM produces a
discovery payload; this module validates it, normalizes citations, and writes
it into AlphaMemory so PathFinder can consume it in later campaigns.
"""

from __future__ import annotations

import uuid
from typing import Any

from factor_lab.alpha.memory import AlphaMemory
from factor_lab.core import config


DISCOVERY_SCHEMA = "factor_lab.alpha.discovery_map.v1"


def ingest_discovery(
    payload: dict[str, Any],
    *,
    memory: AlphaMemory | None = None,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise TypeError("discovery payload must be a dict")
    sources = payload.get("sources") or []
    if not isinstance(sources, list) or not sources:
        raise ValueError("discovery map requires at least one cited source")
    normalized_sources = []
    for source in sources:
        if isinstance(source, str):
            source = {"url": source}
        if not isinstance(source, dict):
            continue
        url = str(source.get("url") or "").strip()
        if not url:
            raise ValueError("every discovery source requires a url")
        normalized_sources.append(
            {
                "url": url,
                "title": source.get("title"),
                "published_at": source.get("published_at"),
                "retrieved_at": source.get("retrieved_at") or config.now_iso(),
                "trust": source.get("trust"),
            }
        )
    if not normalized_sources:
        raise ValueError("discovery map has no usable sources")
    discovery = {
        "schema": DISCOVERY_SCHEMA,
        "discovery_id": f"DISC-{uuid.uuid4().hex[:12]}",
        "brief": payload.get("brief") or {
            "research_question": payload.get("research_question"),
        },
        "clusters": payload.get("clusters") or [],
        "leads": payload.get("leads") or [],
        "gaps": payload.get("gaps") or [],
        "sources": normalized_sources,
        "created_at": config.now_iso(),
    }
    memory = memory or AlphaMemory()
    record = memory.record_discovery(discovery)
    return {"discovery": discovery, "memory_record": record}
