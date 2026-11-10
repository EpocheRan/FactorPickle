"""Build the structured research context consumed by PathFinder and planners.

The context is generated from artifacts, not from a static idea catalog.  It is
the single inheritance surface between rounds: prior hypotheses, failure modes,
data limits, sources, ML results and agent traces.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from factor_lab.core import config
from factor_lab.evaluation import evaluation
from factor_lab.core.store import read_json, write_json_atomic

CONTEXT_PATH = config.LAB_ROOT / "research_context.json"


def _experiment(root: Path) -> dict[str, Any]:
    manifest = read_json(root / "manifest.json") or {}
    hypothesis = read_json(root / "hypothesis.json") or {}
    payload = read_json(root / "report.json") or {}
    report = payload.get("report") or {}
    rating = payload.get("rating") or report.get("rating") or {}
    audit = read_json(root / "audit.json") or {}
    evaluation_payload = read_json(root / "evaluation.json") or evaluation.derive(root)
    trace = read_json(root / "agent_trace.json") or {}
    data_manifest = read_json(root / "data_manifest.json") or {}
    dim = data_manifest.get("data_interface_manifest") or {}
    metrics = audit.get("metrics") or {}
    return {
        "id": root.name,
        "hypothesis_id": manifest.get("hypothesis_id") or hypothesis.get("hypothesis_id"),
        "factor_id": manifest.get("factor_id") or hypothesis.get("factor_id"),
        "title": hypothesis.get("title"),
        "mechanism": hypothesis.get("mechanism"),
        "formula": (hypothesis.get("notes") or ""),
        "data_contract": hypothesis.get("data"),
        "universe": (manifest.get("outcome") or {}).get("universe"),
        "horizon": (manifest.get("outcome") or {}).get("horizon"),
        "outcome": manifest.get("outcome"),
        "metrics": {
            "ic": metrics.get("ic"),
            "ic_t": metrics.get("ic_t"),
            "net": metrics.get("spread_net"),
            "turnover": metrics.get("turnover"),
            "max_rho": metrics.get("max_rho"),
        },
        "rating": rating.get("grade"),
        "score": rating.get("score"),
        "audit": audit.get("verdict"),
        "g3": audit.get("g3"),
        "evaluation_status": evaluation_payload.get("status"),
        "evaluation_reason": evaluation_payload.get("reason"),
        "kill_criteria_triggered": evaluation_payload.get("kill_criteria_triggered") or [],
        "data": {
            "data_path": data_manifest.get("data_path"),
            "services": dim.get("services") or [],
            "codes": len(dim.get("codes") or []),
            "rows": dim.get("rows"),
            "start": data_manifest.get("start"),
            "end": data_manifest.get("end"),
        },
        "agent_trace": trace,
        "artifacts": str(root),
    }


def _coverage() -> dict[str, Any]:
    rows = read_json(config.DATA_COVERAGE_PATH)
    if not isinstance(rows, list):
        rows = []
    return {
        "fields": rows,
        "available_axes": sorted({str(row.get("table", "")) for row in rows if isinstance(row, dict)}),
    }


def _discovery() -> dict[str, Any]:
    raw = read_json(config.LAB_ROOT / "discovery" / "discovery_map.json")
    result = raw if isinstance(raw, dict) else {"leads": [], "sources": []}
    catalog = read_json(config.PROJECT_ROOT / "factor_lab" / "sources" / "catalog.json")
    if isinstance(catalog, dict):
        result["source_catalog"] = catalog
    return result


def _ml_results() -> list[dict[str, Any]]:
    root = config.LAB_ROOT / "ml"
    results: list[dict[str, Any]] = []
    if not root.exists():
        return results
    for path in sorted(root.rglob("*.json")):
        raw = read_json(path)
        if isinstance(raw, dict) and raw.get("schema_version") == "factor_lab.ml_result.v1":
            results.append({"path": str(path), "result": raw})
    return results[-20:]


def _capabilities() -> dict[str, Any]:
    services: list[str] = []
    try:
        from factor_lab.runtime.data_interface_adapter import DataInterfaceGateway

        services = list(DataInterfaceGateway().status().get("services") or [])
    except Exception:
        services = []
    return {
        "services": services,
        "policy": "verified axes are those present in data coverage; unverified services may require a readiness probe",
    }


def build_context(campaign_id: str | None = None) -> dict[str, Any]:
    experiment_roots = [path for path in sorted(config.EXPERIMENTS_DIR.iterdir()) if path.is_dir()] if config.EXPERIMENTS_DIR.exists() else []
    experiments = []
    for path in experiment_roots:
        manifest = read_json(path / "manifest.json") or {}
        if campaign_id and str(manifest.get("campaign_id") or "") != str(campaign_id):
            continue
        experiments.append(_experiment(path))
    failed = [
        {
            "experiment": item["id"],
            "construct": item.get("title") or item.get("factor_id"),
            "factor_id": item.get("factor_id"),
            "reason": item.get("evaluation_reason"),
            "kill_criteria_triggered": item.get("kill_criteria_triggered"),
        }
        for item in experiments
        if item.get("evaluation_status") in {"parked", "failed", "blocked"}
    ]
    contexts = {
        "schema_version": "factor_lab.research_context.v1",
        "generated_at": config.now_iso(),
        "experiments": experiments,
        "failed_constructs": failed,
        "data_coverage": _coverage(),
        "discovery": _discovery(),
        "ml_results": _ml_results(),
        "open_questions": [
            "which data axes remain unvalidated for this campaign?",
            "which failed construct has not been replaced by a genuinely different mechanism?",
        ],
        "failed_families": sorted({str(item.get("factor_id", "")).split(".")[0] for item in failed if item.get("factor_id")}),
    }
    contexts["campaign_id"] = campaign_id
    contexts["data_capabilities"] = _capabilities()
    target = (config.LAB_ROOT / "campaigns" / campaign_id / "research_context.json") if campaign_id else CONTEXT_PATH
    write_json_atomic(target, contexts)
    return contexts
