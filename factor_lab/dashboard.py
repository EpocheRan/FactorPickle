"""Build the human-readable Factor Lab dashboard from artifacts."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import config
from .ledger import Ledger
from .policy import load_policy
from .schemas import OutcomeSpec
from .store import read_json, write_json_atomic


def _read(path: Path) -> dict[str, Any]:
    value = read_json(path)
    return value if isinstance(value, dict) else {}


def _float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        text = str(value).replace("Z", "+00:00")
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except Exception:
        return None


def _age_days(value: Any) -> float | None:
    parsed = _parse_time(value)
    if parsed is None:
        return None
    return round((datetime.now(timezone.utc) - parsed).total_seconds() / 86400.0, 2)


def _primary_stat(report: dict[str, Any], horizon: int) -> dict[str, Any]:
    rows = report.get("ic_stats") or []
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict):
                continue
            try:
                if int(row.get("horizon")) == int(horizon):
                    return row
            except (TypeError, ValueError):
                continue
    return {}


def _size(path: Path) -> tuple[int, int]:
    files = 0
    total = 0
    if not path.exists():
        return 0, 0
    for root, _, names in os.walk(path):
        for name in names:
            try:
                item = Path(root) / name
                total += item.stat().st_size
                files += 1
            except OSError:
                continue
    return files, total


def _storage_snapshot() -> dict[str, Any]:
    root = config.PROJECT_ROOT / "output"
    entries = []
    if root.exists():
        for item in root.iterdir():
            if item.name.startswith("_") and item.name not in {"_archive", "_trash"}:
                continue
            files, total = _size(item) if item.is_dir() else (1, item.stat().st_size if item.exists() else 0)
            entries.append({"name": item.name, "files": files, "bytes": total})
    entries.sort(key=lambda item: item["bytes"], reverse=True)
    total_bytes = sum(item["bytes"] for item in entries)
    return {
        "output_bytes": total_bytes,
        "top": entries[:10],
        "panel_cache": _size(config.PROJECT_ROOT / "output" / "cache" / "research_panel")[1],
    }


def _data_sources(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    """Map a Runner data manifest to FactorPickle data-domain labels."""
    dim = manifest.get("data_interface_manifest") or {}
    services = {str(item) for item in (dim.get("services") or [])}
    fields = {str(item) for item in (dim.get("fields") or [])}
    labels: list[dict[str, Any]] = []

    def add(label: str, matched_fields: list[str], matched_services: list[str]) -> None:
        if not matched_fields and not matched_services:
            return
        labels.append({"label": label, "fields": sorted(set(matched_fields)), "services": sorted(set(matched_services))})

    price_fields = sorted(fields & {"open", "high", "low", "close", "volume", "amount", "turnover"})
    if "market" in services or price_fields:
        add("行情与基础", price_fields, ["market"] if "market" in services else [])

    valuation_fields = sorted(fields & {"float_mv", "total_mv", "pe_ttm", "pb", "ps_ttm", "dv_ttm"})
    if "valuation" in services or valuation_fields:
        add("财务与估值", valuation_fields, ["valuation"] if "valuation" in services else [])

    flow_fields = sorted(fields & {"mf_buy_lg", "mf_buy_elg", "mf_sell_lg", "mf_sell_elg", "margin_rzye", "margin_rqye"})
    if {"moneyflow", "margin"} & services or flow_fields:
        add("资金与情绪", flow_fields, sorted({"moneyflow", "margin"} & services))

    if "concept" in services:
        add("板块与概念", [], ["concept"])
    if "factor" in services:
        add("因子资产", [], ["factor"])
    if labels:
        return labels
    return [{"label": "运行状态", "fields": [], "services": sorted(services)}]


def _load_experiments() -> list[dict[str, Any]]:
    experiments: list[dict[str, Any]] = []
    if not config.EXPERIMENTS_DIR.exists():
        return experiments
    for root in sorted(config.EXPERIMENTS_DIR.iterdir(), reverse=True):
        if not root.is_dir():
            continue
        manifest = _read(root / "manifest.json")
        if not manifest:
            continue
        payload = _read(root / "report.json")
        report = payload.get("report") or {}
        rating = payload.get("rating") or report.get("rating") or {}
        audit = _read(root / "audit.json")
        brief = _read(root / "decision_brief.json")
        decision = _read(root / "decision.json")
        hypothesis = _read(root / "hypothesis.json")
        outcome = manifest.get("outcome") or {}
        horizon = int(outcome.get("horizon") or 5)
        stats = _primary_stat(report, horizon)
        metrics = audit.get("metrics") or {}
        checks = list(audit.get("checks") or []) + list(audit.get("g3_checks") or [])
        grade = rating.get("grade")
        verdict = audit.get("verdict") or "pending"
        g3 = audit.get("g3") or "pending"
        status = manifest.get("status") or "unknown"
        age = _age_days(manifest.get("started_at"))
        exp = {
            "id": root.name,
            "factor": manifest.get("factor_id"),
            "bet": hypothesis.get("bet") or "未分配",
            "hypothesis_id": manifest.get("hypothesis_id"),
            "trial_id": manifest.get("trial_id"),
            "status": status,
            "run_id": manifest.get("run_id"),
            "started_at": manifest.get("started_at"),
            "duration_sec": manifest.get("duration_sec"),
            "age_days": age,
            "horizon": horizon,
            "universe": outcome.get("universe"),
            "ic": _float(metrics.get("ic", stats.get("mean"))),
            "ic_t": _float(metrics.get("ic_t", stats.get("t_indep"))),
            "net": _float(metrics.get("spread_net", (report.get("spread_net") or {}).get("mean"))),
            "turnover": _float(metrics.get("turnover", report.get("turnover"))),
            "capacity": metrics.get("capacity_multiple"),
            "max_rho": metrics.get("max_rho"),
            "g0_5": audit.get("g0_5"),
            "n_trials": audit.get("n_trials"),
            "q_value": audit.get("q_value"),
            "grade": grade,
            "score": rating.get("score"),
            "verdict": verdict,
            "g3": g3,
            "recommend": brief.get("recommend"),
            "decision": decision.get("decision"),
            "gate": brief.get("gate"),
            "agent_trace": _read(root / "agent_trace.json"),
            "data_manifest": _read(root / "data_manifest.json"),
            "data_sources": _data_sources(_read(root / "data_manifest.json")),
            "detail": {
                "audit_checks": checks,
                "brief": brief,
                "decision": decision,
                "manifest": manifest,
                "hypothesis": hypothesis,
                "report": {
                    "ic_stats": report.get("ic_stats"),
                    "quantile_returns": report.get("quantile_returns"),
                    "diagnostics": report.get("diagnostics"),
                    "definition": report.get("definition"),
                    "pool": report.get("pool"),
                    "data_quality": report.get("data_quality"),
                    "spread_gross": report.get("spread_gross"),
                    "spread_net": report.get("spread_net"),
                    "warnings": report.get("warnings"),
                },
                "paths": {
                    "report_html": str(root / "report.html"),
                    "report_json": str(root / "report.json"),
                    "audit_json": str(root / "audit.json"),
                    "orthogonality_json": str(root / "orthogonality.json"),
                    "brief_json": str(root / "decision_brief.json"),
                    "decision_json": str(root / "decision.json"),
                },
            },
        }
        experiments.append(exp)
    experiments.sort(key=lambda item: str(item.get("started_at") or ""), reverse=True)
    return experiments


def _attention(experiments: list[dict[str, Any]], ledger_verification: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for exp in experiments:
        if exp["status"] == "failed":
            items.append({"level": "red", "title": f"{exp['id']} 运行失败", "detail": "检查 manifest 里的 error", "exp_id": exp["id"]})
        if exp["verdict"] == "fail":
            fails = [c.get("check_id") for c in exp["detail"]["audit_checks"] if c.get("status") == "fail"]
            items.append({"level": "red", "title": f"{exp['id']} 审计失败", "detail": "、".join(str(x) for x in fails[:4]), "exp_id": exp["id"]})
        if exp["g3"] == "fail":
            items.append({"level": "red", "title": f"{exp['id']} G3 失败", "detail": "换手/容量不满足交易性", "exp_id": exp["id"]})
        elif exp["g3"] == "warn":
            items.append({"level": "yellow", "title": f"{exp['id']} G3 边界", "detail": "换手/容量缺失或超过上限", "exp_id": exp["id"]})
        if exp.get("max_rho") is not None:
            rho = abs(float(exp["max_rho"]))
            if rho >= 0.9:
                items.append({"level": "red", "title": f"{exp['id']} 相关性硬失败", "detail": f"max|rho|={rho:.2f}；与存量因子重复", "exp_id": exp["id"]})
            elif rho >= 0.7:
                items.append({"level": "yellow", "title": f"{exp['id']} 相关性待复核", "detail": f"max|rho|={rho:.2f}；需要书面理由", "exp_id": exp["id"]})
        if exp["age_days"] is not None and exp["age_days"] > 5 and exp["decision"] in (None, "promote"):
            items.append({"level": "yellow", "title": f"{exp['id']} 停滞", "detail": f"{exp['age_days']:.1f} 天没有最终决策", "exp_id": exp["id"]})
    if not ledger_verification.get("ok"):
        items.append({"level": "red", "title": "账本存在未修复断点", "detail": str(ledger_verification.get("unrepaired")), "exp_id": None})
    if not experiments:
        items.append({"level": "yellow", "title": "还没有实验", "detail": "先提交一个 PathFinder plan 并执行实验", "exp_id": None})
    order = {"red": 0, "yellow": 1, "green": 2}
    items.sort(key=lambda item: order.get(item["level"], 9))
    return items[:12]


def _load_governance() -> dict[str, Any]:
    """Load PathFinder / Supervisor / Builder audit records.

    These records are deliberately separate from experiment artifacts: they
    explain whether an LLM subagent, a deterministic adapter, or a human made
    the decision.  Missing file is a valid state (older labs do not have one).
    """
    path = config.LAB_ROOT / "governance" / "trace.json"
    payload = _read(path)
    if not payload:
        return {"schema_version": "factor_lab.governance_trace.v1", "records": [], "missing": str(path)}
    records = payload.get("records")
    if not isinstance(records, list):
        records = []
    return {"schema_version": payload.get("schema_version"), "records": records, "source_note": payload.get("source_note", "")}


def build_dashboard_data() -> dict[str, Any]:
    policy = load_policy()
    ledger = Ledger()
    verification = ledger.verify()
    summary = ledger.summary()
    experiments = _load_experiments()
    ledger_records = ledger.records()

    hypotheses = [row for row in ledger_records if row.get("event") == "hypothesis_registered"]
    now = datetime.now(timezone.utc)
    weekly_hypotheses = 0
    for row in hypotheses:
        parsed = _parse_time(row.get("ts"))
        if parsed and (now - parsed).days <= 7:
            weekly_hypotheses += 1

    g3_exps = [exp for exp in experiments if exp["verdict"] != "fail" and exp["g3"] != "fail"]
    monthly_g3 = 0
    for exp in g3_exps:
        parsed = _parse_time(exp.get("started_at"))
        if parsed and (now - parsed).days <= 30:
            monthly_g3 += 1

    ages = []
    for exp in g3_exps:
        created = _parse_time((exp.get("detail", {}).get("hypothesis") or {}).get("created_at"))
        finished = _parse_time(exp.get("started_at"))
        if created and finished:
            ages.append(max(0.0, (finished - created).total_seconds() / 86400.0))
    time_to_g3 = round(sum(ages) / len(ages), 2) if ages else None

    fail_count = sum(1 for exp in experiments if exp["verdict"] == "fail")
    audit_fail_rate = round(fail_count / len(experiments), 3) if experiments else 0.0
    orphan_count = 0
    for exp in experiments:
        for check in exp["detail"]["audit_checks"]:
            if check.get("check_id") == "orphan_run" and check.get("status") == "fail":
                orphan_count += 1

    if not verification.get("ok") or any(exp["verdict"] == "fail" or exp["status"] == "failed" for exp in experiments):
        health_level = "red"
    elif not experiments or any(exp["verdict"] == "warn" or exp["g3"] in ("warn", "fail") for exp in experiments):
        health_level = "yellow"
    else:
        health_level = "green"

    reasons = []
    if not verification.get("ok"):
        reasons.append("账本存在未修复断点")
    if not experiments:
        reasons.append("还没有实验")
    if any(exp["verdict"] == "fail" for exp in experiments):
        reasons.append("至少一个审计硬失败")
    if any(exp["g3"] in ("warn", "fail") for exp in experiments):
        reasons.append("G3 交易性尚未完全证明")
    if health_level == "green":
        reasons.append("完整性检查通过；产出指标见下方")

    seen_trials: set[str] = set()
    points = []
    for record in ledger_records:
        if record.get("event") != "run_started":
            continue
        payload = record.get("payload") or {}
        trial = str(payload.get("trial_id") or "")
        if trial and trial not in seen_trials:
            seen_trials.add(trial)
            points.append({"ts": record.get("ts"), "n": len(seen_trials), "trial_id": trial})

    g2_pass = sum(1 for exp in experiments if exp["verdict"] != "fail")
    g05_pass = sum(1 for exp in experiments if exp.get("g0_5") == "pass")
    g3_pass = sum(1 for exp in experiments if exp["verdict"] != "fail" and exp["g3"] == "pass")
    g4_pass = sum(1 for exp in experiments if exp["decision"] == "promote")
    funnel = [
        {"gate": "G0", "count": len(experiments), "note": "注册 / 运行"},
        {"gate": "G0.5", "count": g05_pass, "note": "暴露相关通过"},
        {"gate": "G2", "count": g2_pass, "note": "审计无硬失败"},
        {"gate": "G3", "count": g3_pass, "note": "交易性通过"},
        {"gate": "G4", "count": g4_pass, "note": "决策 promote"},
    ]

    bets: dict[str, dict[str, Any]] = {}
    for exp in experiments:
        bet = str(exp.get("bet") or "未分配")
        item = bets.setdefault(bet, {"bet": bet, "experiments": 0, "trial_ids": set(), "g3": 0, "status": "explore"})
        item["experiments"] += 1
        if exp.get("trial_id"):
            item["trial_ids"].add(str(exp["trial_id"]))
        if exp["verdict"] != "fail" and exp["g3"] == "pass":
            item["g3"] += 1
        if exp.get("decision") == "promote":
            item["status"] = "confirmed"
        elif exp["verdict"] != "fail" and exp.get("g3") in ("pass", "warn") and item["status"] != "confirmed":
            item["status"] = "exploit"
    for item in bets.values():
        item["trials"] = len(item.pop("trial_ids"))

    findings = []
    for exp in experiments:
        for check in exp["detail"]["audit_checks"]:
            if check.get("status") in ("fail", "warn"):
                findings.append(
                    {
                        "exp_id": exp["id"],
                        "check_id": check.get("check_id"),
                        "title": check.get("title"),
                        "status": check.get("status"),
                        "actual": check.get("actual"),
                        "expected": check.get("expected"),
                    }
                )

    coverage = read_json(config.DATA_COVERAGE_PATH)
    if not isinstance(coverage, list):
        coverage = []
    portfolio = read_json(config.PORTFOLIO_PATH)
    if not isinstance(portfolio, list):
        portfolio = list(bets.values())

    research_funnel = [
        {
            "stage": "假设",
            "count": len({str(exp.get("hypothesis_id")) for exp in experiments if exp.get("hypothesis_id")}),
            "note": "已注册的研究问题",
        },
        {
            "stage": "因子",
            "count": len({str(exp.get("factor")) for exp in experiments if exp.get("factor")}),
            "note": "Builder 落库或被评估的因子",
        },
        {
            "stage": "实验",
            "count": len(experiments),
            "note": "一次预注册 outcome",
        },
        {
            "stage": "评估",
            "count": sum(1 for exp in experiments if exp.get("grade")),
            "note": "有评级 / 审计结果",
        },
        {
            "stage": "决策",
            "count": sum(1 for exp in experiments if exp.get("decision")),
            "note": "park / iterate / promote",
        },
    ]

    return {
        "meta": {
            "generated_at": config.now_iso(),
            "git_commit": config.git_commit(),
            "policy_version": policy.get("version"),
            "policy_hash": policy.get("sha256"),
            "ledger_head": verification.get("head"),
            "experiment_count": len(experiments),
        },
        "health": {"level": health_level, "reasons": reasons},
        "research_funnel": research_funnel,
        "experiments": experiments,
        "control": read_json(config.CONTROL_PATH) or {"state": "not_configured"},
        "heartbeat": read_json(config.HEARTBEAT_PATH),
        "cycle": read_json(config.CYCLE_PATH),
        "directive": read_json(config.DIRECTIVE_PATH),
        "quarantine": read_json(config.QUARANTINE_PATH),
        "ledger": summary,
        "governance": _load_governance(),
    }


def render_dashboard(data: dict[str, Any] | None = None, *, out_path: Path | None = None) -> Path:
    payload = data or build_dashboard_data()
    template_path = Path(__file__).with_name("templates") / "dashboard.html"
    template = template_path.read_text(encoding="utf-8")
    data_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    html = template.replace("__LAB_DATA__", data_json)
    html = html.replace("__GENERATED_AT__", str(payload["meta"]["generated_at"]))
    target = out_path or config.DASHBOARD_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(html, encoding="utf-8")
    write_json_atomic(config.DASHBOARD_DATA_PATH, payload)
    return target
