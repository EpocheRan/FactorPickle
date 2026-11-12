"""Web Gateway discovery contracts and deterministic validation (P3).

The Gateway/LLM produces a cited discovery payload; this module validates it,
normalizes citations, compiles candidate plans and checks provenance/novelty.
No network calls and no raw HTML ever enter the research context.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from pathlib import Path
from typing import Any, Iterable

from factor_lab.alpha.memory import AlphaMemory
from factor_lab.core import config


DISCOVERY_SCHEMA = "factor_lab.alpha.discovery_map.v1"
BRIEF_SCHEMA = "factor_lab.alpha.discovery_brief.v1"
CANDIDATE_SCHEMA = "factor_lab.alpha.candidate_plan.v1"
NOVELTY_SCHEMA = "factor_lab.alpha.novelty_check.v1"
VALID_SIGNS = {"positive", "negative", "both", "unknown"}
_HTMLISH = re.compile(r"<\s*/?\s*(script|html|body|div|span|p|a|img|style)\b", re.IGNORECASE)


def _text(value: Any) -> str:
    return " ".join(str(value or "").split())


def _clip(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def _lead_id(seed: str) -> str:
    return f"LEAD-{hashlib.sha256(seed.encode('utf-8')).hexdigest()[:10]}"


def _normalize_sources(raw_sources: Iterable[Any], fallback_retrieved_at: str | None = None) -> list[dict[str, Any]]:
    sources = []
    for source in raw_sources or []:
        if isinstance(source, str):
            source = {"url": source}
        if not isinstance(source, dict):
            continue
        url = _text(source.get("url"))
        if not url:
            raise ValueError("every discovery source requires a url")
        if not url.startswith(("http://", "https://")):
            raise ValueError(f"discovery source url must be http(s): {url}")
        retrieved_at = _text(source.get("retrieved_at") or fallback_retrieved_at or "") or config.now_iso()
        sources.append(
            {
                "url": url,
                "title": _text(source.get("title")) or None,
                "published_at": source.get("published_at"),
                "retrieved_at": retrieved_at,
                "trust": source.get("trust"),
            }
        )
    return sources


def validate_discovery_map(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate/normalize a discovery_map; return {ok, issues, map}."""
    issues: list[str] = []
    if not isinstance(payload, dict):
        return {"ok": False, "issues": ["discovery map must be an object"], "map": None}
    top_sources = _normalize_sources(payload.get("sources") or [], payload.get("retrieved_at"))
    leads = payload.get("leads") or []
    if not isinstance(leads, list) or not leads:
        issues.append("discovery map requires at least one lead")
    normalized_leads = []
    seen: set[str] = set()
    for index, lead in enumerate(leads):
        if not isinstance(lead, dict):
            issues.append(f"lead {index} must be an object")
            continue
        title = _text(lead.get("title") or lead.get("mechanism") or "")
        mechanism = _text(lead.get("mechanism") or title)
        if not mechanism:
            issues.append(f"lead {index} has no mechanism")
            continue
        if _HTMLISH.search(mechanism) or _HTMLISH.search(title):
            issues.append(f"lead {index} contains raw HTML")
        sources = _normalize_sources(lead.get("sources") or top_sources, lead.get("retrieved_at") or payload.get("retrieved_at"))
        if not sources:
            issues.append(f"lead {index} requires at least one cited source")
        lead_id = _text(lead.get("lead_id")) or _lead_id(f"{title}|{mechanism}")
        if lead_id in seen:
            issues.append(f"duplicate lead_id: {lead_id}")
        seen.add(lead_id)
        sign = _text(lead.get("expected_sign") or "unknown").lower()
        if sign not in VALID_SIGNS:
            issues.append(f"lead {index} expected_sign must be one of {sorted(VALID_SIGNS)}")
        normalized_leads.append(
            {
                "lead_id": lead_id,
                "title": title,
                "mechanism": mechanism,
                "expected_sign": sign,
                "sources": sources,
                "confidence": _clip(lead.get("confidence")),
                "tags": [_text(tag) for tag in (lead.get("tags") or []) if _text(tag)],
            }
        )
    normalized = {
        "schema": DISCOVERY_SCHEMA,
        "research_question": payload.get("research_question"),
        "clusters": payload.get("clusters") or [],
        "leads": normalized_leads,
        "gaps": payload.get("gaps") or [],
        "sources": top_sources,
        "retrieved_at": payload.get("retrieved_at") or config.now_iso(),
    }
    return {"ok": not issues, "issues": issues, "map": normalized}


def validate_discovery_brief(payload: dict[str, Any]) -> dict[str, Any]:
    issues: list[str] = []
    if not isinstance(payload, dict):
        return {"ok": False, "issues": ["brief must be an object"], "brief": None}
    mechanisms = payload.get("candidate_mechanisms") or []
    if not isinstance(mechanisms, list) or not mechanisms:
        issues.append("brief requires candidate_mechanisms")
    normalized = []
    for index, item in enumerate(mechanisms):
        if not isinstance(item, dict):
            issues.append(f"mechanism {index} must be an object")
            continue
        mechanism = _text(item.get("mechanism"))
        if not mechanism:
            issues.append(f"mechanism {index} has no name")
            continue
        sign = _text(item.get("expected_sign") or "unknown").lower()
        if sign not in VALID_SIGNS:
            issues.append(f"mechanism {index} invalid expected_sign")
        lead_ids = [_text(x) for x in (item.get("lead_ids") or []) if _text(x)]
        if not lead_ids:
            issues.append(f"mechanism {index} requires lead_ids from the map")
        normalized.append(
            {
                "mechanism": mechanism,
                "expected_sign": sign,
                "fields_hint": [_text(field) for field in (item.get("fields_hint") or []) if _text(field)],
                "lead_ids": lead_ids,
                "kill_criteria": [_text(x) for x in (item.get("kill_criteria") or []) if _text(x)],
            }
        )
    result = {
        "schema": BRIEF_SCHEMA,
        "research_question": payload.get("research_question"),
        "constraints": payload.get("constraints") or [],
        "known_dead_ends": payload.get("known_dead_ends") or [],
        "candidate_mechanisms": normalized,
    }
    return {"ok": not issues, "issues": issues, "brief": result}


def compile_candidates(brief: dict[str, Any], discovery_map: dict[str, Any]) -> dict[str, Any]:
    """brief + map -> candidate plans carrying lead provenance (no DSL)."""
    brief_check = validate_discovery_brief(brief)
    map_check = validate_discovery_map(discovery_map)
    if not brief_check["ok"] or not map_check["ok"]:
        raise ValueError(f"brief/map invalid: {brief_check['issues']} {map_check['issues']}")
    leads = {lead["lead_id"]: lead for lead in map_check["map"]["leads"]}
    plans = []
    seen: set[str] = set()
    for mechanism in brief_check["brief"]["candidate_mechanisms"]:
        lead_ids = [lead_id for lead_id in mechanism["lead_ids"] if lead_id in leads]
        if not lead_ids:
            continue
        name = mechanism["mechanism"]
        if name in seen:
            continue
        seen.add(name)
        plans.append(
            {
                "schema": CANDIDATE_SCHEMA,
                "mechanism": name,
                "expected_sign": mechanism["expected_sign"],
                "fields_hint": mechanism["fields_hint"],
                "kill_criteria": mechanism["kill_criteria"],
                "lead_ids": lead_ids,
                "sources": [
                    source
                    for lead_id in lead_ids
                    for source in leads[lead_id]["sources"]
                ],
            }
        )
    return {
        "schema": "factor_lab.alpha.candidate_plans.v1",
        "research_question": brief_check["brief"].get("research_question"),
        "candidates": plans,
        "n_candidates": len(plans),
    }


def attach_provenance(spec: dict[str, Any], candidate_plan: dict[str, Any]) -> dict[str, Any]:
    payload = dict(spec or {})
    payload["provenance"] = {
        "schema": "factor_lab.alpha.provenance.v1",
        "mechanism": candidate_plan.get("mechanism"),
        "lead_ids": candidate_plan.get("lead_ids") or [],
        "sources": candidate_plan.get("sources") or [],
        "expected_sign": candidate_plan.get("expected_sign"),
    }
    return payload


def _library_expressions(library_root: str | Path | None = None) -> list[dict[str, str]]:
    from factor_lab.alpha.factor_library import list_factors

    rows = []
    for summary in list_factors(library_root=library_root):
        path = Path(summary.get("path") or "")
        dsl = path / "factor.dsl"
        if dsl.exists():
            expression = dsl.read_text(encoding="utf-8").strip()
            if expression:
                rows.append({"factor_id": summary.get("factor_id"), "expression": expression})
    return rows


def _tokens(expression: str) -> set[str]:
    return set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", expression))


def novelty_check(
    expression: str,
    *,
    context: Any | None = None,
    library_root: str | Path | None = None,
    structural_threshold: float = 0.80,
    correlation_threshold: float = 0.80,
    sample_every: int = 20,
    exclude_factor_ids: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Structural + optional factor-correlation novelty against the library."""
    candidate_tokens = _tokens(expression)
    structural = []
    correlations = []
    excluded = set(str(item) for item in (exclude_factor_ids or []))
    for item in _library_expressions(library_root):
        if str(item.get("factor_id")) in excluded:
            continue
        tokens = _tokens(item["expression"])
        union = candidate_tokens | tokens
        similarity = len(candidate_tokens & tokens) / len(union) if union else 0.0
        structural.append({"factor_id": item["factor_id"], "expression": item["expression"], "similarity": similarity})
        if context is not None:
            try:
                import pandas as pd

                from factor_lab.alpha.compiler import compile_expression

                left, _ = compile_expression(expression, context)
                right, _ = compile_expression(item["expression"], context)
                dates = list(left.index[:: max(1, int(sample_every))])
                values = []
                for date in dates:
                    pair = pd.concat([left.loc[date].rename("a"), right.loc[date].rename("b")], axis=1).dropna()
                    if len(pair) >= 10:
                        values.append(abs(float(pair["a"].rank().corr(pair["b"].rank()))))
                if values:
                    correlations.append({"factor_id": item["factor_id"], "abs_rank_corr": float(sum(values) / len(values))})
            except Exception:  # noqa: BLE001
                continue
    max_structural = max((row["similarity"] for row in structural), default=0.0)
    max_corr = max((row["abs_rank_corr"] for row in correlations), default=0.0) if correlations else None
    passed = max_structural <= structural_threshold and (max_corr is None or max_corr <= correlation_threshold)
    return {
        "schema": NOVELTY_SCHEMA,
        "expression": expression,
        "structural_threshold": structural_threshold,
        "correlation_threshold": correlation_threshold,
        "max_structural_similarity": max_structural,
        "max_abs_rank_corr": max_corr,
        "structural": sorted(structural, key=lambda row: row["similarity"], reverse=True)[:5],
        "correlations": sorted(correlations, key=lambda row: row["abs_rank_corr"], reverse=True)[:5],
        "passed_novelty": passed,
    }


def ingest_discovery(
    payload: dict[str, Any],
    *,
    memory: AlphaMemory | None = None,
) -> dict[str, Any]:
    """Validate a discovery map and write it into AlphaMemory."""
    check = validate_discovery_map(payload)
    if not check["ok"]:
        raise ValueError("; ".join(check["issues"]))
    discovery = dict(check["map"])
    discovery["discovery_id"] = f"DISC-{uuid.uuid4().hex[:12]}"
    discovery["created_at"] = config.now_iso()
    memory = memory or AlphaMemory()
    record = memory.record_discovery(discovery)
    return {"discovery": discovery, "memory_record": record}
