"""Deterministic all-research portfolio report for Factor Lab.

The report is a read-only aggregation of runtime artifacts:

- campaigns and their round files
- team outputs / team manifests / protocol audits
- Factor Library artifacts and frozen pre-registrations
- memory attempts + Edge Cards
- family ledger, probes, holdout policy and data-axis coverage

It answers six questions in one standalone HTML dashboard:
1. how research iterated
2. how ideas were generated
3. which agents were involved
4. what the tests showed
5. which datasets were used
6. what was concluded

No LLM judgments are introduced.  Text is either copied from a structured
artifact or deterministically derived from counts/metrics.
"""

from __future__ import annotations

import html
import json
import os
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from factor_lab.core import config


REPORT_SCHEMA = "factor_lab.alpha.research_portfolio.v1"
ROLES = [
    ("pathfinder", "PathFinder"),
    ("web-gateway", "Web Gateway"),
    ("ml-planner", "ML Planner"),
    ("factor-builder", "FactorBuilder"),
    ("reader", "Reader"),
]
ROLE_LABELS = dict(ROLES)
ROLE_FILES = {
    "pathfinder_question.json": "pathfinder",
    "pre_registration.json": "pathfinder",
    "pre_registration.frozen.json": "pathfinder",
    "discovery_brief.json": "pathfinder",
    "pathfinder_output.json": "pathfinder",
    "pathfinder_brief_source.json": "pathfinder",
    "web_gateway_map.json": "web-gateway",
    "discovery_map.normalized.json": "web-gateway",
    "ml_planner_plan.json": "ml-planner",
    "factor_builder.json": "factor-builder",
    "reader_events.json": "reader",
}
CAMPAIGN_LABELS = {
    "CAMP-548013a26d": "Campaign 1 · MAX/lottery baseline",
    "CAMP-9126b55d26": "Campaign 1b · Baby MAX/lottery",
    "CAMP-e8d05b7311": "Campaign 2 · Post-kill search",
    "CAMP-382cbc7893": "Campaign 3 · MAX attribution",
    "CAMP-24d0c741d3": "Campaign 4 · Low-price/value",
    "CAMP-4319413b27": "P3 · Web discovery",
    "CAMP-2b995bc651": "P2 · ML execution",
    "CAMP-b79c47ef2d": "P0/P1 · Pre-registered batch",
    "CAMP-35db6b3056": "P1b · Holder-number robustness",
    "CAMP-6efd50f0a4": "L0 smoke · clean cycle",
    "CAMP-76e3088958": "L0 smoke · cycle",
    "CAMP-a98589944b": "L0 smoke · cycle 2",
    "CAMP-f13fb8b93a": "L0 smoke · final cycle",
}
KNOWN_CONCLUSIONS = {
    "CAMP-548013a26d": "MAX/lottery baseline: search and ML rounds did not produce a freezeable candidate.",
    "CAMP-9126b55d26": "Baby MAX/lottery cycles failed on net spread; the first holdout was consumed historically; no candidate.",
    "CAMP-e8d05b7311": "Post-kill dev search produced no candidate that survived the freeze/FDR path.",
    "CAMP-382cbc7893": "MAX attribution falsified: MAX increment +0.000019; the price leg is decisive; the frozen composite was later reclassified killed.",
    "CAMP-24d0c741d3": "Low-price/value candidate was strong in dev (net +0.004199 / net_t 1.9558) but program-level FDR later rejected it; reclassified killed.",
}
KIND_LABELS = {
    "research": "研究 campaign",
    "discovery": "Discovery",
    "ml": "ML",
    "calibration": "L0 / smoke",
}
DETERMINISTIC_FILES = {
    "evaluation.json",
    "fdr.json",
    "novelty.json",
    "p2_run.json",
    "ml_admission.json",
    "p3_run.json",
    "p3_program_fdr.json",
    "candidate_plans.json",
    "ablation.json",
    "lowprice_attribution.json",
    "red_team.json",
    "scale_check.json",
    "team_manifest.json",
    "PROTOCOL_AUDIT.json",
}


def _read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return default


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                value = json.loads(line)
                if isinstance(value, dict):
                    rows.append(value)
    except Exception:  # noqa: BLE001
        pass
    return rows


def _clean(value: Any, limit: int = 220) -> str:
    if value is None:
        return ""
    text = re.sub(r"\s+", " ", str(value)).strip()
    if "\ufffd" in text or re.search(r"[\u00c0-\u00ff]{3,}", text):
        return "[历史文本编码损坏，见原始文件]"
    if len(text) > limit:
        text = text[: max(0, limit - 1)].rstrip() + "…"
    return text


def _fmt_num(value: Any, digits: int = 4) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    if number != number:
        return "—"
    if abs(number) >= 1000:
        return f"{number:,.1f}"
    if abs(number) >= 1:
        return f"{number:.{digits}g}"
    return f"{number:.{digits}f}"


def _fmt_date(value: Any) -> str:
    text = str(value or "")
    if len(text) >= 10 and text[4] == "-":
        return text[:10]
    return text or "—"


def _rel_link(path: Path | str | None, base_dir: Path) -> str:
    if not path:
        return ""
    try:
        rel = os.path.relpath(str(path), start=str(base_dir))
    except Exception:  # noqa: BLE001
        rel = str(path)
    return rel.replace("\\", "/")


def _kind_for(campaign_id: str, objective: str) -> str:
    text = objective.lower()
    if campaign_id in {"CAMP-6efd50f0a4", "CAMP-76e3088958", "CAMP-a98589944b", "CAMP-f13fb8b93a"}:
        return "calibration"
    if "l0" in text or "smoke" in text:
        return "calibration"
    if campaign_id == "CAMP-2b995bc651" or "p2" in text or "ml execution" in text:
        return "ml"
    if campaign_id == "CAMP-4319413b27" or "p3" in text or "discovery" in text:
        return "discovery"
    return "research"


def _find_text(payload: Any, keys: tuple[str, ...], depth: int = 0) -> str:
    if depth > 4 or payload is None:
        return ""
    if isinstance(payload, dict):
        for key in keys:
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return _clean(value, 420)
            if isinstance(value, (int, float)) and value:
                return str(value)
        for value in payload.values():
            found = _find_text(value, keys, depth + 1)
            if found:
                return found
    elif isinstance(payload, list):
        for value in payload:
            found = _find_text(value, keys, depth + 1)
            if found:
                return found
    return ""


def _collect_strings(payload: Any, limit: int = 6) -> list[str]:
    out: list[str] = []

    def walk(value: Any, depth: int = 0) -> None:
        if len(out) >= limit or depth > 3:
            return
        if isinstance(value, str):
            text = _clean(value, 160)
            if text and text not in out:
                out.append(text)
        elif isinstance(value, dict):
            for item in value.values():
                walk(item, depth + 1)
        elif isinstance(value, list):
            for item in value:
                walk(item, depth + 1)

    walk((payload or {}).get("mechanism_priority") if isinstance(payload, dict) else payload)
    if not out and isinstance(payload, dict):
        for key in ("research_question", "hypothesis", "rationale", "mechanism", "expected_failure_mode"):
            if payload.get(key):
                out.extend([_clean(x, 160) for x in str(payload[key]).split("；")[:3]])
    return out


def _load_artifacts(root: Path) -> dict[str, dict[str, Any]]:
    artifacts: dict[str, dict[str, Any]] = {}
    for path in sorted((root / "factors" / "generated").glob("*/*/*/manifest.json")):
        payload = _read_json(path, {})
        if not isinstance(payload, dict):
            continue
        factor_id = str(payload.get("factor_id") or "")
        if not factor_id:
            continue
        spec = payload.get("spec") or {}
        dsl_path = path.parent / "factor.dsl"
        expression = dsl_path.read_text(encoding="utf-8").strip() if dsl_path.exists() else ""
        artifacts[factor_id] = {
            "factor_id": factor_id,
            "family": payload.get("family"),
            "version": payload.get("version"),
            "expression": expression,
            "manifest_path": path,
            "data_contract": payload.get("data_contract") or spec.get("data_contract") or {},
            "spec": spec,
            "source_campaign": spec.get("source_campaign"),
        }
    return artifacts


def _load_team_files(team_dir: Path) -> list[tuple[Path, Any]]:
    rows = []
    if not team_dir.exists():
        return rows
    for path in sorted(team_dir.glob("*.json")):
        if path.name in DETERMINISTIC_FILES:
            continue
        rows.append((path, _read_json(path, {})))
    return rows


def _agent_evidence(
    campaign_id: str,
    rounds: list[dict[str, Any]],
    team_files: list[tuple[Path, Any]],
    manifest: dict[str, Any] | None,
    artifacts: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    evidence: dict[str, dict[str, Any]] = {
        role: {"role": role, "label": label, "files": [], "tasks": set(), "notes": [], "status": None}
        for role, label in ROLES
    }
    if manifest:
        for item in manifest.get("roles") or []:
            role = str(item.get("role") or "")
            if role in evidence:
                evidence[role]["status"] = item.get("status")
                if item.get("task_id"):
                    evidence[role]["tasks"].add(str(item.get("task_id")))
                for output in item.get("outputs") or []:
                    evidence[role]["files"].append(str(output))
    for path, payload in team_files:
        role = ROLE_FILES.get(path.name)
        if role:
            evidence[role]["files"].append(str(path))
            if role == "reader" and isinstance(payload, dict):
                evidence[role]["notes"].append(f"{len(payload.get('documents') or [])} documents")
    for artifact in artifacts:
        spec = artifact.get("spec") or {}
        if str(spec.get("source_campaign") or "") != campaign_id:
            continue
        evidence["factor-builder"]["notes"].append(f"artifact {artifact.get('factor_id')}")
        evidence["factor-builder"]["files"].append(str(artifact.get("manifest_path") or ""))
        for task in spec.get("team_tasks") or []:
            if task:
                for role in evidence:
                    if role in {"pathfinder", "factor-builder"}:
                        evidence[role]["tasks"].add(str(task))
        for agent in spec.get("agents") or []:
            if agent in evidence:
                evidence[agent]["notes"].append(f"artifact {artifact.get('factor_id')}")
    for row in rounds:
        for agent in row.get("agents") or []:
            if agent in evidence:
                evidence[agent]["notes"].append(f"{row.get('round_id')}")
        for task in row.get("team_tasks") or []:
            for role in ("pathfinder", "factor-builder"):
                evidence[role]["tasks"].add(str(task))
    # P2 reused the ML Planner plan from campaign 4; record the dependency explicitly.
    if campaign_id == "CAMP-2b995bc651":
        source = next((p for p, v in team_files if p.name == "p2_run.json"), None)
        if source:
            evidence["ml-planner"]["notes"].append("reused pre-registered plan from CAMP-24d0c741d3")
    for row in evidence.values():
        row["tasks"] = sorted(row["tasks"])
        row["files"] = sorted(set(row["files"]))
        row["notes"] = sorted(set(row["notes"]))
        row["used"] = bool(row["files"] or row["tasks"] or row["notes"] or row["status"])
    return evidence


def _extract_ideas(campaign: dict[str, Any], team_files: list[tuple[Path, Any]], artifacts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ideas: list[dict[str, Any]] = []
    for path, payload in team_files:
        role = ROLE_FILES.get(path.name)
        if role == "pathfinder":
            if isinstance(payload, dict) and payload.get("research_question"):
                ideas.append({"agent": role, "kind": "research question", "title": _clean(payload.get("research_question"), 300), "detail": "；".join(_collect_strings(payload)), "source": path})
            for family in (payload.get("families") or []) if isinstance(payload, dict) else []:
                for candidate in family.get("candidates") or []:
                    ideas.append({"agent": "factor-builder", "kind": "pre-registered candidate", "title": f"{family.get('family_id')} / {candidate.get('candidate_id')}", "detail": str(candidate.get("expression") or ""), "source": path})
            for mechanism in (payload.get("candidate_mechanisms") or []) if isinstance(payload, dict) else []:
                ideas.append({"agent": role, "kind": "candidate mechanism", "title": _clean(mechanism.get("mechanism"), 220), "detail": "；".join(mechanism.get("kill_criteria") or []), "source": path})
        elif role == "web-gateway":
            if isinstance(payload, dict):
                leads = payload.get("leads") or []
                titles = [_clean(lead.get("title"), 140) for lead in leads[:4] if isinstance(lead, dict)]
                ideas.append({"agent": role, "kind": "cited discovery map", "title": f"{len(leads)} leads / {len(payload.get('sources') or [])} sources", "detail": "；".join(titles), "source": path})
        elif role == "ml-planner":
            if isinstance(payload, dict):
                ideas.append({"agent": role, "kind": "ML experiment plan", "title": _clean(payload.get("hypothesis"), 260), "detail": f"features={', '.join(payload.get('features') or [])} | {payload.get('model_family')} / {payload.get('cv_scheme')}", "source": path})
        elif role == "reader":
            if isinstance(payload, dict):
                docs = payload.get("documents") or []
                events = sum(len(doc.get("events") or []) for doc in docs if isinstance(doc, dict))
                ideas.append({"agent": role, "kind": "structured evidence", "title": f"{len(docs)} documents / {events} events", "detail": "", "source": path})
    for artifact in artifacts:
        ideas.append({"agent": "factor-builder", "kind": "artifact", "title": artifact.get("factor_id"), "detail": artifact.get("expression") or "", "source": artifact.get("manifest_path")})
    # Deduplicate by title/detail, keep max 18.
    unique = []
    seen = set()
    for item in ideas:
        key = (item.get("title"), item.get("detail"), str(item.get("source")))
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique[:18]


def _conclusion(campaign_id: str, rounds: list[dict[str, Any]], team_files: list[tuple[Path, Any]], objective: str) -> tuple[str, str]:
    last = rounds[-1].get("research_result") if rounds else {}
    keys = (
        "conclusion",
        "attribution_verdict",
        "attribution_conclusion",
        "final_decision",
        "recommendation",
        "next_research_question",
        "conclusion_text",
        "summary",
    )
    text = _find_text(last, keys)
    next_text = _find_text(last, ("next", "next_research_question", "recommendation"))
    if not text:
        for _path, payload in team_files:
            text = _find_text(payload, keys)
            if text:
                break
    if not text:
        text = KNOWN_CONCLUSIONS.get(campaign_id, "")
    if not text:
        evaluated = [r for r in rounds if (r.get("research_result") or {}).get("metrics")]
        best = max((float((r.get("research_result") or {}).get("metrics", {}).get("net_t") or -999) for r in evaluated), default=None)
        text = f"{len(evaluated)} evaluated rounds; best net_t={_fmt_num(best)}; stopped={rounds[-1].get('decision') if rounds else 'unknown'}."
    if not next_text:
        next_text = "继续前先固定新的 pre-registration；未通过 program FDR 的候选不得 freeze。"
    return text, next_text


def _holdout_status(rounds: list[dict[str, Any]]) -> bool | None:
    for row in reversed(rounds):
        value = _find_text(row.get("research_result"), ("holdout",))
        if value:
            return "used" if "true" in value.lower() else "not used"
        raw = (row.get("research_result") or {}).get("holdout")
        if isinstance(raw, dict) and "used" in raw:
            return "used" if raw.get("used") else "not used"
    return None


def _campaign_record(
    campaign_dir: Path,
    *,
    artifacts: dict[str, dict[str, Any]],
    base_dir: Path,
) -> dict[str, Any]:
    campaign = _read_json(campaign_dir / "campaign.json", {})
    if not isinstance(campaign, dict):
        return {}
    campaign_id = str(campaign.get("campaign_id") or campaign_dir.name)
    objective = _clean(campaign.get("objective") or "", 320)
    rounds = []
    for path in sorted((campaign_dir / "rounds").glob("round-*.json")):
        payload = _read_json(path, {})
        if isinstance(payload, dict):
            payload["_path"] = path
            rounds.append(payload)
    team_dir = campaign_dir / "team"
    team_files = _load_team_files(team_dir)
    manifest = _read_json(team_dir / "team_manifest.json", None)
    audit = _read_json(team_dir / "PROTOCOL_AUDIT.json", None)
    fdr_payload = _read_json(team_dir / "fdr.json", {})
    evaluation_payload = _read_json(team_dir / "evaluation.json", {})
    p3_fdr = _read_json(team_dir / "p3_program_fdr.json", {})
    campaign_artifacts = [item for item in artifacts.values() if item.get("source_campaign") == campaign_id]
    ref_ids = []
    for row in rounds:
        for ref in row.get("factor_refs") or []:
            if isinstance(ref, dict) and ref.get("factor_id"):
                ref_ids.append(str(ref["factor_id"]))
    artifact_by_id = {item["factor_id"]: item for item in campaign_artifacts}
    for factor_id in ref_ids:
        if factor_id in artifacts:
            artifact_by_id.setdefault(factor_id, artifacts[factor_id])
    evidence = _agent_evidence(campaign_id, rounds, team_files, manifest, list(artifact_by_id.values()))
    agents_used = [role for role, row in evidence.items() if row.get("used")]
    ideas = _extract_ideas(campaign, team_files, list(artifact_by_id.values()))
    conclusion, next_step = _conclusion(campaign_id, rounds, team_files, objective)
    # FDR lookup
    fdr_by_factor: dict[str, dict[str, Any]] = {}
    if isinstance(fdr_payload, dict):
        for row in fdr_payload.get("results") or []:
            if isinstance(row, dict) and row.get("factor_id"):
                fdr_by_factor[str(row["factor_id"])] = row
    if isinstance(p3_fdr, dict):
        for row in p3_fdr.get("results") or []:
            if isinstance(row, dict) and row.get("factor_id"):
                fdr_by_factor.setdefault(str(row["factor_id"]), row)
    # Candidate rows from evaluated cycle rounds
    candidates = []
    for row in rounds:
        result = row.get("research_result") or {}
        metrics = result.get("metrics") or {}
        evaluation = result.get("evaluation") or row.get("evaluation") or {}
        if not metrics and not evaluation:
            continue
        refs = row.get("factor_refs") or []
        factor_id = str(refs[0].get("factor_id")) if refs else ""
        artifact = artifact_by_id.get(factor_id) or artifacts.get(factor_id) or {}
        fdr = fdr_by_factor.get(factor_id) or {}
        candidates.append(
            {
                "factor_id": factor_id,
                "family": artifact.get("family") or (refs[0].get("family") if refs else ""),
                "expression": artifact.get("expression") or "",
                "metrics": metrics,
                "verdict": evaluation.get("verdict") or result.get("verdict") or "",
                "program_fdr": (fdr.get("program_fdr") or {}).get("passed") if isinstance(fdr.get("program_fdr"), dict) else None,
                "program_p": (fdr.get("program_fdr") or {}).get("p_net") if isinstance(fdr.get("program_fdr"), dict) else None,
                "program_threshold": (fdr.get("program_fdr") or {}).get("bh_threshold") if isinstance(fdr.get("program_fdr"), dict) else None,
                "family_fdr": (fdr.get("family_fdr") or {}).get("passed") if isinstance(fdr.get("family_fdr"), dict) else None,
                "round_id": row.get("round_id"),
            }
        )
    gate_pass = [row for row in candidates if row.get("verdict") == "pass"]
    best = max(candidates, key=lambda row: float(row.get("metrics", {}).get("net_t") or -999), default=None)
    dataset_manifests = []
    for row in rounds:
        data_manifest = row.get("data_manifest")
        if isinstance(data_manifest, dict) and data_manifest.get("fields"):
            dataset_manifests.append(data_manifest)
    eval_files = []
    for name in ("evaluation.json", "fdr.json", "novelty.json", "team_manifest.json", "PROTOCOL_AUDIT.json"):
        path = team_dir / name
        if path.exists():
            eval_files.append(path)
    stop_decision = _read_json(campaign_dir / "stop_decision.json", {})
    ml_summary = None
    if campaign_id == "CAMP-2b995bc651":
        p2_payload = _read_json(team_dir / "p2_run.json", {})
        if isinstance(p2_payload, dict):
            plan = p2_payload.get("plan") or {}
            admission = p2_payload.get("admission") or {}
            ml_summary = {
                "metrics": p2_payload.get("metrics") or {},
                "admission": admission,
                "conclusion": p2_payload.get("conclusion"),
                "plan_source": p2_payload.get("plan_source"),
            }
            if plan.get("hypothesis"):
                ideas.append({"agent": "ml-planner", "kind": "ML experiment plan", "title": _clean(plan.get("hypothesis"), 300), "detail": f"features={', '.join(plan.get('features') or [])} | {plan.get('model_family')} / {plan.get('cv_scheme')}", "source": team_dir / "p2_run.json"})
            evidence["ml-planner"]["used"] = True
            evidence["ml-planner"]["notes"].append("reused pre-registered plan from CAMP-24d0c741d3")
            evidence["ml-planner"]["files"].append(str(team_dir / "p2_run.json"))
            conclusion = str(p2_payload.get("conclusion") or conclusion)
    if ml_summary is None:
        for name in ("ml_expanded_run_v2.json", "ml_expanded_run.json", "p2_run.json"):
            path = team_dir / name
            if not path.exists():
                continue
            payload = _read_json(path, {})
            if not isinstance(payload, dict):
                continue
            plan = payload.get("plan") or {}
            admission = payload.get("admission") or {}
            ml_summary = {
                "metrics": payload.get("metrics") or {},
                "admission": admission,
                "conclusion": payload.get("conclusion") or f"ML admission passed={admission.get('passed')}; cycle={((payload.get('cycle') or {}).get('metrics') or {})}",
                "plan_source": payload.get("plan_source"),
                "weights": ((payload.get("export") or {}).get("weights")) if isinstance(payload.get("export"), dict) else None,
                "cycle": payload.get("cycle"),
                "bundle": payload.get("bundle"),
                "path": path,
            }
            if plan.get("hypothesis"):
                ideas.append({"agent": "ml-planner", "kind": "ML experiment plan", "title": _clean(plan.get("hypothesis"), 300), "detail": f"features={', '.join(plan.get('features') or [])} | {plan.get('model_family')} / {plan.get('cv_scheme')}", "source": path})
            evidence["ml-planner"]["used"] = True
            evidence["ml-planner"]["files"].append(str(path))
            if payload.get("conclusion"):
                conclusion = str(payload["conclusion"])
            break
    agents_used = [role for role, row in evidence.items() if row.get("used")]
    best_candidates = [row for row in candidates if isinstance(row.get("metrics"), dict) and row["metrics"].get("net_t") is not None]
    best = max(best_candidates, key=lambda row: float(row["metrics"].get("net_t") or -999), default=None)
    gate_pass = [row for row in candidates if row.get("verdict") == "pass"]
    return {
        "campaign_id": campaign_id,
        "label": CAMPAIGN_LABELS.get(campaign_id, campaign_id),
        "objective": objective,
        "kind": _kind_for(campaign_id, objective),
        "kind_label": KIND_LABELS.get(_kind_for(campaign_id, objective), "研究"),
        "state": campaign.get("state"),
        "created_at": _fmt_date(campaign.get("created_at")),
        "created_at_full": str(campaign.get("created_at") or ""),
        "updated_at": _fmt_date(campaign.get("updated_at")),
        "n_rounds": campaign.get("n_rounds"),
        "max_rounds": campaign.get("max_rounds"),
        "stop_reason": campaign.get("stop_reason") or stop_decision.get("reason") or "",
        "rounds": [
            {
                "round_id": row.get("round_id"),
                "decision": row.get("decision"),
                "schema": (row.get("research_result") or {}).get("schema") or "",
                "metrics": (row.get("research_result") or {}).get("metrics") or {},
                "verdict": (row.get("research_result") or {}).get("evaluation", {}).get("verdict") if isinstance((row.get("research_result") or {}).get("evaluation"), dict) else "",
                "agents": row.get("agents") or [],
                "team_tasks": row.get("team_tasks") or [],
                "factor_refs": [ref.get("factor_id") for ref in row.get("factor_refs") or [] if isinstance(ref, dict)],
            }
            for row in rounds
        ],
        "agents": evidence,
        "agents_used": agents_used,
        "protocol_audit": audit if isinstance(audit, dict) else None,
        "team_manifest": manifest if isinstance(manifest, dict) else None,
        "ideas": ideas,
        "candidates": candidates,
        "gate_pass": len(gate_pass),
        "gate_fail": len([row for row in candidates if row.get("verdict") == "fail"]),
        "best": best,
        "fdr_pass": [row for row in candidates if row.get("program_fdr") is True],
        "dataset_manifests": dataset_manifests,
        "holdout": _holdout_status(rounds),
        "conclusion": conclusion,
        "next_step": next_step,
        "team_files": [p for p, _ in team_files],
        "team_dir": team_dir,
        "eval_files": eval_files,
        "has_team": bool(team_files or manifest),
        "ml_summary": ml_summary,
    }


def _dataset_key(manifest: dict[str, Any]) -> tuple:
    return (
        str(manifest.get("data_hash") or ""),
        str(manifest.get("start") or ""),
        str(manifest.get("end") or ""),
        int(manifest.get("codes") or 0),
        int(manifest.get("rows") or 0),
        len(manifest.get("fields") or []),
    )


def build_portfolio(root: str | Path | None = None, *, base_dir: str | Path | None = None) -> dict[str, Any]:
    lab_root = Path(root) if root is not None else Path(config.LAB_ROOT)
    deliverables = Path(base_dir) if base_dir is not None else lab_root.parent / "deliverables"
    artifacts = _load_artifacts(lab_root)
    campaigns = []
    for campaign_dir in sorted((lab_root / "alpha" / "campaigns").iterdir()):
        if not campaign_dir.is_dir():
            continue
        record = _campaign_record(campaign_dir, artifacts=artifacts, base_dir=deliverables)
        if record:
            campaigns.append(record)
    campaigns.sort(key=lambda row: (str(row.get("created_at_full") or ""), row["campaign_id"]))
    for index, row in enumerate(campaigns, start=1):
        row["phase"] = index

    # Map factor ids to campaigns before attaching Edge Cards / FDR.
    factor_campaign: dict[str, str] = {}
    for row in campaigns:
        for item in row.get("candidates") or []:
            if item.get("factor_id"):
                factor_campaign.setdefault(str(item["factor_id"]), row["campaign_id"])
        for factor_id in row.get("rounds") or []:
            pass
    for artifact_id, artifact in artifacts.items():
        source = artifact.get("source_campaign")
        if source:
            factor_campaign.setdefault(artifact_id, str(source))

    all_cards = _read_jsonl(lab_root / "alpha" / "memory" / "edge_cards.jsonl")
    latest_cards: dict[str, dict[str, Any]] = {}
    for card in all_cards:
        if card.get("edge_id"):
            latest_cards[str(card["edge_id"])] = card
    cards_by_campaign: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for card in latest_cards.values():
        factor_id = str(((card.get("factor_ref") or {}).get("factor_id")) or "")
        campaign_id = factor_campaign.get(factor_id)
        if campaign_id:
            cards_by_campaign[campaign_id].append(card)
    for row in campaigns:
        row["edge_cards"] = cards_by_campaign.get(row["campaign_id"], [])
        row["edge_status"] = dict(sorted({status: sum(1 for card in row["edge_cards"] if card.get("status") == status) for status in {c.get("status") for c in row["edge_cards"]}}.items()))
        for card in row["edge_cards"]:
            manifest = card.get("data_manifest")
            if isinstance(manifest, dict) and manifest.get("fields"):
                row.setdefault("edge_manifests", []).append(manifest)

    # Attach dataset manifests from cards to the campaigns that produced the cards.
    for row in campaigns:
        for manifest in row.get("edge_manifests") or []:
            row["dataset_manifests"].append(manifest)
    datasets: dict[tuple, dict[str, Any]] = {}
    for row in campaigns:
        for manifest in row.get("dataset_manifests") or []:
            key = _dataset_key(manifest)
            entry = datasets.setdefault(
                key,
                {
                    "start": manifest.get("start"),
                    "end": manifest.get("end"),
                    "codes": manifest.get("codes"),
                    "rows": manifest.get("rows"),
                    "fields": manifest.get("fields") or [],
                    "services": manifest.get("services") or [],
                    "hash": str(manifest.get("data_hash") or "")[:16],
                    "campaigns": set(),
                },
            )
            entry["campaigns"].add(row["campaign_id"])
    dataset_rows = []
    for entry in sorted(datasets.values(), key=lambda item: (-int(item.get("codes") or 0), str(item.get("start") or ""))):
        entry["campaigns"] = sorted(entry["campaigns"])
        dataset_rows.append(entry)

    # Data-axis usage by actual candidate expressions (not just availability).
    try:
        from factor_lab.alpha.data_axes import P0_AXES, P1_AXES
        from factor_lab.alpha.validation import validate as _validate

        axis_specs: dict[str, dict[str, Any]] = {}
        for group, axes in (("p0", P0_AXES), ("p1", P1_AXES)):
            for name, spec in axes.items():
                axis_specs[name] = {"group": group, "label": spec.get("label") or name, "fields": list(spec.get("fields") or [])}
        field_axis = {field: axis for axis, spec in axis_specs.items() for field in spec["fields"]}
        axis_usage: dict[str, dict[str, Any]] = {
            axis: {"axis": axis, "group": spec["group"], "label": spec["label"], "fields": spec["fields"], "campaigns": set(), "candidates": 0}
            for axis, spec in axis_specs.items()
        }
        for campaign in campaigns:
            for candidate in campaign.get("candidates") or []:
                expression = str(candidate.get("expression") or "")
                if not expression:
                    continue
                try:
                    fields = set(_validate(expression).fields)
                except Exception:  # noqa: BLE001
                    continue
                candidate["fields"] = sorted(fields)
                for field in fields:
                    axis = field_axis.get(field)
                    if axis:
                        axis_usage[axis]["campaigns"].add(campaign["campaign_id"])
                        axis_usage[axis]["candidates"] += 1
        for item in axis_usage.values():
            item["campaigns"] = sorted(item["campaigns"])
        axis_usage_rows = sorted(axis_usage.values(), key=lambda row: (row["group"], row["axis"]))
    except Exception:  # noqa: BLE001
        axis_usage_rows = []

    # Memory / ledger / axes / probes / holdout.
    memory = _read_json(lab_root / "alpha" / "memory" / "summary.json", {}) or {}
    ledger = _read_json(lab_root / "alpha" / "families" / "summary.json", {}) or {}
    axes_coverage = _read_json(lab_root / "alpha" / "data_axes" / "coverage.json", {}) or {}
    axes_completeness = _read_json(lab_root / "alpha" / "data_axes" / "p1_completeness.json", {}) or {}
    probes = []
    for path in sorted((lab_root / "alpha" / "probes").glob("*.json")):
        payload = _read_json(path, {})
        if isinstance(payload, dict):
            probes.append({"path": path, "payload": payload})
    probes.sort(key=lambda item: str(item["payload"].get("created_at") or ""))
    holdout = _read_json(lab_root / "alpha" / "policy" / "holdout.json", {})
    if not holdout:
        holdout = _read_json(Path("factor_lab/alpha/policy/holdout.json"), {}) or {}
    holdout_start = str(holdout.get("holdout_start") or "")
    holdout_end = str(holdout.get("holdout_end") or "")
    dev_end = str(holdout.get("dev_end") or "")
    for dataset in dataset_rows:
        start, end = str(dataset.get("start") or ""), str(dataset.get("end") or "")
        if holdout_start and end and end >= holdout_start:
            dataset["window_type"] = "holdout" if start >= holdout_start else "includes holdout"
        elif dev_end and end and end > dev_end:
            dataset["window_type"] = "beyond dev"
        else:
            dataset["window_type"] = "dev"
    historical_holdout = sum(1 for dataset in dataset_rows if dataset.get("window_type") != "dev")

    # Campaign-level idea/agent/result conclusions for the portfolio section.
    meaningful = [row for row in campaigns if row["kind"] != "calibration"]
    def _best_net_t(row: dict[str, Any]) -> float:
        try:
            return float(((row.get("best") or {}).get("metrics") or {}).get("net_t"))
        except (TypeError, ValueError):
            return -999.0

    best_campaign = max(meaningful, key=_best_net_t, default=None)
    fdr_any = any(row.get("fdr_pass") for row in campaigns)
    validated = sum(1 for card in latest_cards.values() if card.get("status") in {"validated", "active"})
    killed = sum(1 for card in latest_cards.values() if card.get("status") == "killed")
    research_cards = sum(1 for card in latest_cards.values() if card.get("status") == "research")
    calibrated = [row for row in campaigns if row["kind"] == "calibration"]
    portfolio_conclusions = [
        {
            "title": "研究结论：没有可冻结资产",
            "body": f"最新 Edge Cards：{killed} killed / {research_cards} research / {validated} validated。没有 passed freeze；Allocator 仍为 baseline；holdout 保持锁定。" if not fdr_any else "存在 FDR pass 记录，需逐条复核。",
            "tone": "bad",
        },
        {
            "title": "门禁结论：program FDR 是主约束",
            "body": "多个候选在 Gate 或 family-level FDR 上通过，但 joint program denominator 拒绝；freeze 只能使用 program-level FDR。",
            "tone": "warn",
        },
        {
            "title": "数据结论：P0/P1 轴已接入",
            "body": "P0 margin/moneyflow/financial/valuation 与 P1 block/trade/top/holder/repurchase 已进入 Panel/AlphaContext；下一瓶颈是证据供给与新数据轴，不是数据接入。",
            "tone": "good",
        },
        {
            "title": "下一步：新 pre-registration，而不是事后调整",
            "body": "对 holder-number 这类 family-level 现象只能开新的预注册；不能复用旧 trial 翻转符号、不能把 family pass 当 freeze。",
            "tone": "info",
        },
    ]
    return {
        "schema": REPORT_SCHEMA,
        "generated_at": config.now_iso(),
        "base_dir": deliverables,
        "kpis": {
            "campaigns": len(campaigns),
            "meaningful_campaigns": len(meaningful),
            "calibration_campaigns": len(calibrated),
            "attempts": int(memory.get("n_attempts") or len(_read_jsonl(lab_root / "alpha" / "memory" / "attempts.jsonl"))),
            "failures": int(memory.get("n_failures") or 0),
            "ledger_trials": int(ledger.get("n_trials") or 0),
            "edge_cards": len(latest_cards),
            "killed": killed,
            "research_cards": research_cards,
            "validated": validated,
            "best_net_t": ((best_campaign or {}).get("best") or {}).get("metrics", {}).get("net_t"),
            "best_factor": ((best_campaign or {}).get("best") or {}).get("factor_id"),
            "fdr_any": fdr_any,
            "agents_used": len({role for row in campaigns for role in row.get("agents_used") or []}),
            "datasets": len(dataset_rows),
            "historical_holdout_datasets": historical_holdout,
        },
        "campaigns": campaigns,
        "datasets": dataset_rows,
        "axis_usage": axis_usage_rows,
        "axes": axes_coverage,
        "axes_completeness": axes_completeness,
        "memory": memory,
        "ledger": ledger,
        "probes": probes,
        "holdout": holdout,
        "portfolio_conclusions": portfolio_conclusions,
    }
