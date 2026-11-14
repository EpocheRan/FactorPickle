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

from factor_lab.alpha.governance import git_commit, holdout_uses, policy_snapshot
from factor_lab.core import config


REPORT_SCHEMA = "factor_lab.alpha.research_portfolio.v1"


def _load_local_annotations() -> dict[str, Any]:
    """Load optional research annotations from the gitignored runtime workspace.

    Campaign labels, conclusions and run windows are research data, not code.
    They live under ``output/factor_lab/alpha/research_data`` so the report
    logic can be committed without shipping research findings or campaign ids.
    Missing annotations degrade to generic campaign ids/empty conclusions.
    """
    path = Path(config.LAB_ROOT) / "alpha" / "research_data" / "portfolio_annotations.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - optional local data
        return {}
    return payload if isinstance(payload, dict) else {}


_ANNOTATIONS = _load_local_annotations()
RUN_START_UTC = str(_ANNOTATIONS.get("run_start_utc") or "")
RUN_END_UTC = str(_ANNOTATIONS.get("run_end_utc") or "")
CAMPAIGN_LABELS = {
    str(key): str(value) for key, value in (_ANNOTATIONS.get("campaign_labels") or {}).items()
}
KNOWN_CONCLUSIONS = {
    str(key): str(value) for key, value in (_ANNOTATIONS.get("known_conclusions") or {}).items()
}
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


def _run_group(created_at: str | None) -> str:
    """Group campaigns into the local run window, post-run replication, or legacy."""
    if not RUN_START_UTC or not RUN_END_UTC:
        return "legacy"
    try:
        value = datetime.fromisoformat(str(created_at or "").replace("Z", "+00:00"))
        start = datetime.fromisoformat(RUN_START_UTC)
        end = datetime.fromisoformat(RUN_END_UTC)
        if start <= value <= end:
            return "nine_hour_run"
        if value > end:
            return "post_run_replication"
        return "legacy"
    except Exception:  # noqa: BLE001
        return "legacy"


def _kind_for(campaign_id: str, objective: str) -> str:
    import re

    text = objective.lower()
    if campaign_id in {"CAMP-6efd50f0a4", "CAMP-76e3088958", "CAMP-a98589944b", "CAMP-f13fb8b93a"}:
        return "calibration"
    if "l0" in text or "smoke" in text:
        return "calibration"
    if campaign_id == "CAMP-2b995bc651" or "p2" in text or "ml execution" in text or text.startswith("ml ") or "machine learning" in text or "ml expanded" in text or "ml branch" in text or "ml experiment" in text or re.search(r"\bml\b", text):
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
        spec_agents = [str(item) for item in (spec.get("agents") or []) if item in evidence]
        spec_tasks = [str(item) for item in (spec.get("team_tasks") or []) if item]
        for agent, task in zip(spec_agents, spec_tasks):
            evidence[agent]["tasks"].add(task)
        for agent in spec_agents:
            evidence[agent]["notes"].append(f"artifact {artifact.get('factor_id')}")
    for row in rounds:
        agents = [str(agent) for agent in (row.get("agents") or [])]
        tasks = [str(task) for task in (row.get("team_tasks") or [])]
        for agent in agents:
            if agent in evidence:
                evidence[agent]["notes"].append(f"{row.get('round_id')}")
        # Pair by index; do not attribute one task to every role.
        for agent, task in zip(agents, tasks):
            if agent in evidence and task:
                evidence[agent]["tasks"].add(task)
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
        def _long_only_t(round_row: dict[str, Any]) -> float:
            result = round_row.get("research_result") or {}
            long_only = round_row.get("long_only") or result.get("long_only") or {}
            size = long_only.get("size_screened") or {}
            try:
                return float(size.get("long_only_t"))
            except (TypeError, ValueError):
                return -999.0

        gate_pass_rounds = [r for r in evaluated if _round_long_only_verdict(r) == "pass"]
        best = max((_long_only_t(r) for r in (gate_pass_rounds or evaluated)), default=None)
        text = f"{len(evaluated)} evaluated rounds; best long-only t={_fmt_num(best)}; stopped={rounds[-1].get('decision') if rounds else 'unknown'}."
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


def _two_sided_p(value: Any) -> float | None:
    from statistics import NormalDist

    try:
        z = abs(float(value))
        if z != z or z == float("inf"):
            return None
        return float(2.0 * (1.0 - NormalDist().cdf(z)))
    except (TypeError, ValueError):
        return None


def _one_sided_p(value: Any) -> float | None:
    from statistics import NormalDist

    try:
        z = float(value)
        if z != z or z == float("inf"):
            return None
        return float(1.0 - NormalDist().cdf(z)) if z > 0 else 1.0
    except (TypeError, ValueError):
        return None


def _round_long_only_verdict(row: dict[str, Any]) -> str:
    result = row.get("research_result") or {}
    metrics = result.get("metrics") or {}
    promotion = row.get("promotion") or result.get("promotion") or {}
    coverage = metrics.get("coverage")
    try:
        coverage_ok = coverage is not None and float(coverage) >= 0.90
    except (TypeError, ValueError):
        coverage_ok = False
    return "pass" if promotion.get("verdict") == "pass" and coverage_ok else "fail"


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
        long_only = row.get("long_only") or result.get("long_only") or {}
        size_screened = long_only.get("size_screened") or {}
        promotion = row.get("promotion") or result.get("promotion") or {}
        coverage = metrics.get("coverage")
        long_only_pass = bool(
            promotion.get("verdict") == "pass"
            and coverage is not None
            and float(coverage) >= 0.90
        )
        primary_verdict = "pass" if long_only_pass else ("insufficient_evidence" if coverage is None else "fail")
        candidates.append(
            {
                "factor_id": factor_id,
                "family": artifact.get("family") or (refs[0].get("family") if refs else ""),
                "expression": artifact.get("expression") or "",
                "metrics": metrics,
                "long_only": long_only,
                "promotion": promotion,
                "long_only_t": size_screened.get("long_only_t"),
                "long_only_net_excess_annualized": size_screened.get("long_only_net_excess_annualized"),
                "coverage": coverage,
                "verdict": primary_verdict,
                "research_verdict": evaluation.get("verdict") or result.get("verdict") or "",
                "long_short_verdict": evaluation.get("long_short_verdict") or evaluation.get("verdict") or "",
                "program_fdr": (fdr.get("program_fdr") or {}).get("passed") if isinstance(fdr.get("program_fdr"), dict) else None,
                "program_p": (fdr.get("program_fdr") or {}).get("p_net") if isinstance(fdr.get("program_fdr"), dict) else None,
                "program_threshold": (fdr.get("program_fdr") or {}).get("bh_threshold") if isinstance(fdr.get("program_fdr"), dict) else None,
                "p_two_sided": _two_sided_p(metrics.get("net_t")),
                "p_one_sided_positive": _one_sided_p(metrics.get("net_t")),
                "family_fdr": (fdr.get("family_fdr") or {}).get("passed") if isinstance(fdr.get("family_fdr"), dict) else None,
                "round_id": row.get("round_id"),
            }
        )
    gate_pass = [row for row in candidates if row.get("verdict") == "pass"]
    best = max(candidates, key=lambda row: float(row.get("long_only_t") or -999), default=None)
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
        for name in ("external_run.json", "h20_run.json", "singlefold_run.json", "rank_native_run.json", "ml_10y_summary.json", "ml_10y_run_v3.json", "ml_10y_run.json", "ml_expanded_run_v2.json", "ml_expanded_run.json", "p2_run.json"):
            path = team_dir / name
            if not path.exists():
                continue
            payload = _read_json(path, {})
            if not isinstance(payload, dict):
                continue
            plan = payload.get("plan") or {}
            admission = payload.get("admission") or payload.get("ml_admission") or {}
            metrics = payload.get("metrics") or payload.get("ml_metrics") or {}
            export_payload = payload.get("ml_export") or {}
            fidelity = export_payload.get("fidelity") or payload.get("ml_fidelity")
            export_error = export_payload.get("error") if isinstance(export_payload, dict) else None
            conclusion = payload.get("conclusion")
            if not conclusion:
                conclusion = (
                    f"ML admission passed={admission.get('passed')}; exported fidelity="
                    f"{((fidelity or {}).get('mean_rank_corr'))}; "
                    f"export_error={export_error or 'none'}"
                )
            ml_summary = {
                "metrics": metrics,
                "admission": admission,
                "conclusion": conclusion or f"ML admission passed={admission.get('passed')}; cycle={((payload.get('cycle') or {}).get('metrics') or {})}",
                "plan_source": payload.get("plan_source"),
                "fidelity": fidelity,
                "export": export_payload if isinstance(export_payload, dict) else None,
                "weights": ((payload.get("export") or {}).get("weights")) if isinstance(payload.get("export"), dict) else None,
        "standardized_weights": ((payload.get("export") or {}).get("standardized_weights")) if isinstance(payload.get("export"), dict) else None,
                "standardized_weights": ((payload.get("export") or {}).get("standardized_weights")) if isinstance(payload.get("export"), dict) else None,
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
    best_candidates = [row for row in candidates if row.get("long_only_t") is not None]
    gate_pass_candidates = [row for row in best_candidates if row.get("verdict") == "pass"]
    best = max(gate_pass_candidates or best_candidates, key=lambda row: float(row["long_only_t"] or -999), default=None)
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
                "long_only": row.get("long_only") or (row.get("research_result") or {}).get("long_only"),
                "promotion": row.get("promotion") or (row.get("research_result") or {}).get("promotion"),
                "evaluation_universe": (row.get("research_result") or {}).get("evaluation_universe"),
                "research_verdict": (row.get("research_result") or {}).get("evaluation", {}).get("verdict") if isinstance((row.get("research_result") or {}).get("evaluation"), dict) else "",
                "verdict": _round_long_only_verdict(row),
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
        row["run_group"] = _run_group(row.get("created_at_full"))

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
    from factor_lab.alpha.trial_sink import event_summary as _event_summary

    trial_events = _event_summary()
    ledger_meta = _read_json(lab_root / "alpha" / "families" / "summary.json", {}) or {}
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
    policy = policy_snapshot()
    try:
        from factor_lab.alpha.gates import alpha_gate_spec, load_gate_policy
        _gate = load_gate_policy()
        cost_policy = {"gate": _gate.to_dict(), "cost": alpha_gate_spec().cost.to_dict()}
    except Exception:  # noqa: BLE001
        cost_policy = {}
    huses = holdout_uses()
    # Program-level population: use the effective expression count, not an
    # arbitrary "first ledger row" candidate.
    from factor_lab.alpha.fdr import estimate_n_eff as _estimate_n_eff, load_family_policy as _load_family_policy, load_ledger as _load_ledger
    try:
        _ledger = _load_ledger()
        _expressions = [str(row.get("canonical_expression") or row.get("expression") or "") for row in _ledger if row.get("canonical_expression") or row.get("expression")]
        _n_eff = _estimate_n_eff(_expressions)
        _family_policy = _load_family_policy()
        _q = float(_family_policy.get("q") or 0.10)
        from statistics import NormalDist

        fdr_population = {
            "n_trials": len(_ledger),
            "n_unique": int(_n_eff.get("n_unique") or 0),
            "n_eff": int(_n_eff.get("n_eff") or 0),
            "q": _q,
            "rank": 1,
            "bh_threshold": None,
            "bh_threshold_t": None,
            "rank1_p": (_q / float(_n_eff.get("n_eff"))) if _n_eff.get("n_eff") else None,
        }
        if fdr_population["rank1_p"] is not None:
            fdr_population["rank1_t"] = NormalDist().inv_cdf(1.0 - float(fdr_population["rank1_p"]) / 2.0)
    except Exception:  # noqa: BLE001
        fdr_population = {}
    # Edge Cards that cannot be attributed to a campaign (rendered explicitly, never dropped).
    attributed_card_ids = {str(card.get("edge_id")) for row in campaigns for card in row.get("edge_cards") or []}
    unattributed_cards = sorted({str(card.get("edge_id")) for card in latest_cards.values() if str(card.get("edge_id")) not in attributed_card_ids})

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
            "body": f"最新 Edge Cards：{killed} killed / {research_cards} research / {validated} validated。long-only 是主证据，long-short 仅诊断；没有通过 long-only program FDR + 独立确认的候选，Allocator 仍为 baseline，holdout 保持锁定。" if not fdr_any else "存在 FDR pass 记录，需逐条复核。",
            "tone": "bad",
        },
        {
            "title": "long-only primary：Gate 之后还有 FDR 和独立确认",
            "body": "long-only net excess 是主统计量；long-short 只作诊断。promotion 需要 long-only Gate + coverage + long-only program FDR + 独立验证，Gate pass 不等于 validated。",
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
            "nine_hour_campaigns": sum(1 for row in campaigns if row.get("run_group") == "nine_hour_run"),
            "post_run_campaigns": sum(1 for row in campaigns if row.get("run_group") == "post_run_replication"),
            "legacy_campaigns": sum(1 for row in campaigns if row.get("run_group") == "legacy"),
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
            "best_long_only_t": ((best_campaign or {}).get("best") or {}).get("long_only_t"),
            "best_factor": ((best_campaign or {}).get("best") or {}).get("factor_id"),
            "fdr_any": fdr_any,
            "agents_used": len({role for row in campaigns for role in row.get("agents_used") or []}),
            "datasets": len(dataset_rows),
            "historical_holdout_datasets": historical_holdout,
            "holdout_uses": len(huses),
            "audited_campaigns": sum(1 for row in campaigns if isinstance(row.get("protocol_audit"), dict)),
            "audit_ok": sum(1 for row in campaigns if isinstance(row.get("protocol_audit"), dict) and row["protocol_audit"].get("ok")),
            "audit_failed": sum(1 for row in campaigns if isinstance(row.get("protocol_audit"), dict) and not row["protocol_audit"].get("ok")),
            "unattributed_cards": len(unattributed_cards),
            "trial_events": trial_events.get("n"),
            "trial_events_by_stage": trial_events.get("by_stage"),
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
        "holdout_uses": huses,
        "policy": policy,
        "cost_policy": cost_policy,
        "git_commit": git_commit(),
        "ledger_hash": ledger_meta.get("ledger_hash"),
        "fdr_population": fdr_population,
        "unattributed_cards": unattributed_cards,
        "portfolio_conclusions": portfolio_conclusions,
    }
