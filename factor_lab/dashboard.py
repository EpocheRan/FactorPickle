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
            "bet": hypothesis.get("bet") or "unassigned",
            "hypothesis_id": manifest.get("hypothesis_id"),
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
            "n_trials": audit.get("n_trials"),
            "q_value": audit.get("q_value"),
            "grade": grade,
            "score": rating.get("score"),
            "verdict": verdict,
            "g3": g3,
            "recommend": brief.get("recommend"),
            "decision": decision.get("decision"),
            "gate": brief.get("gate"),
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
            items.append({"level": "red", "title": f"{exp['id']} run failed", "detail": "inspect manifest error", "exp_id": exp["id"]})
        if exp["verdict"] == "fail":
            fails = [c.get("check_id") for c in exp["detail"]["audit_checks"] if c.get("status") == "fail"]
            items.append({"level": "red", "title": f"{exp['id']} audit fail", "detail": ", ".join(str(x) for x in fails[:4]), "exp_id": exp["id"]})
        if exp["g3"] == "fail":
            items.append({"level": "red", "title": f"{exp['id']} G3 fail", "detail": "turnover/capacity not tradeable", "exp_id": exp["id"]})
        elif exp["g3"] == "warn":
            items.append({"level": "yellow", "title": f"{exp['id']} G3 boundary", "detail": "turnover/capacity missing or above cap", "exp_id": exp["id"]})
        if exp["age_days"] is not None and exp["age_days"] > 5 and exp["decision"] in (None, "promote"):
            items.append({"level": "yellow", "title": f"{exp['id']} stale", "detail": f"{exp['age_days']:.1f} days without a final decision", "exp_id": exp["id"]})
    if not ledger_verification.get("ok"):
        items.append({"level": "red", "title": "ledger chain has unrepaired gaps", "detail": str(ledger_verification.get("unrepaired")), "exp_id": None})
    if not experiments:
        items.append({"level": "yellow", "title": "No experiments yet", "detail": "run the pilot or seed artifacts", "exp_id": None})
    order = {"red": 0, "yellow": 1, "green": 2}
    items.sort(key=lambda item: order.get(item["level"], 9))
    return items[:12]


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
        reasons.append("ledger chain has unrepaired gaps")
    if not experiments:
        reasons.append("no experiments yet")
    if any(exp["verdict"] == "fail" for exp in experiments):
        reasons.append("at least one audit hard fail")
    if any(exp["g3"] in ("warn", "fail") for exp in experiments):
        reasons.append("G3 tradeability is not fully proven")
    if health_level == "green":
        reasons.append("integrity checks pass; output metrics are in the dashboard")

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
    g3_pass = sum(1 for exp in experiments if exp["verdict"] != "fail" and exp["g3"] == "pass")
    g4_pass = sum(1 for exp in experiments if exp["decision"] == "promote")
    funnel = [
        {"gate": "G0", "count": len(experiments), "note": "registered / run"},
        {"gate": "G0.5", "count": len(experiments), "note": "P0 manual rho"},
        {"gate": "G2", "count": g2_pass, "note": "audit not hard-fail"},
        {"gate": "G3", "count": g3_pass, "note": "tradeability pass"},
        {"gate": "G4", "count": g4_pass, "note": "decision promote"},
    ]

    bets: dict[str, dict[str, Any]] = {}
    for exp in experiments:
        bet = str(exp.get("bet") or "unassigned")
        item = bets.setdefault(bet, {"bet": bet, "experiments": 0, "trials": 0, "g3": 0, "status": "explore"})
        item["experiments"] += 1
        item["trials"] += int(exp.get("n_trials") or 0)
        if exp["verdict"] != "fail" and exp["g3"] == "pass":
            item["g3"] += 1
        if exp.get("decision") == "promote":
            item["status"] = "confirmed"
        elif exp.get("g3") in ("pass", "warn") and item["status"] != "confirmed":
            item["status"] = "exploit"

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
        "kpis": [
            {"label": "Weekly hypotheses", "value": weekly_hypotheses, "sub": "target: >= 3", "tone": "ok" if weekly_hypotheses >= 3 else "warn"},
            {"label": "Monthly G3 candidates", "value": monthly_g3, "sub": "target: >= 1", "tone": "ok" if monthly_g3 >= 1 else "warn"},
            {"label": "Hypothesis -> G3", "value": f"{time_to_g3:.1f}d" if time_to_g3 is not None else "n/a", "sub": "target: <= 5 working days", "tone": "ok" if time_to_g3 is not None and time_to_g3 <= 5 else "warn"},
            {"label": "Distinct trials", "value": summary.get("distinct_trials"), "sub": "ledger trial = hypothesis x outcome", "tone": "ok"},
            {"label": "Orphan runs", "value": orphan_count, "sub": "must be 0", "tone": "bad" if orphan_count else "ok"},
            {"label": "Audit fail rate", "value": f"{audit_fail_rate:.0%}", "sub": f"{fail_count}/{len(experiments) or 0} experiments", "tone": "bad" if fail_count else "ok"},
        ],
        "funnel": funnel,
        "experiments": experiments,
        "attention": _attention(experiments, verification),
        "bets": list(bets.values()),
        "portfolio": portfolio,
        "audit_findings": findings[:80],
        "ledger_points": points,
        "coverage": coverage,
        "storage": _storage_snapshot(),
        "control": read_json(config.CONTROL_PATH) or {"state": "not_configured"},
        "ledger": summary,
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
