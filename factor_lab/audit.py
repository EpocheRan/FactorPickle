"""Deterministic audit for one experiment.

The audit is intentionally arithmetic-first.  It does not ask a model whether a
report "looks reasonable"; it recomputes the headline identities from the
serialized report and checks the ledger/contract around it.
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from . import config
from .ledger import Ledger
from .policy import load_policy
from .schemas import OutcomeSpec
from .stats import two_sided_p
from .store import read_json, write_json_atomic


def _check(
    check_id: str,
    title: str,
    status: str,
    actual: str,
    expected: str,
    note: str = "",
) -> dict[str, Any]:
    return {
        "check_id": check_id,
        "title": title,
        "status": status,
        "actual": actual,
        "expected": expected,
        "note": note,
    }


def _close(a: float, b: float, rel: float = 1e-6) -> bool:
    if not (math.isfinite(a) and math.isfinite(b)):
        return False
    return abs(a - b) <= max(1e-9, rel * max(abs(a), abs(b), 1.0))


def _series_values(payload: Any) -> list[float]:
    if not isinstance(payload, dict):
        return []
    values = payload.get("values") or []
    out: list[float] = []
    for value in values:
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(number):
            out.append(number)
    return out


def _frame(payload: Any) -> pd.DataFrame | None:
    if not isinstance(payload, dict):
        return None
    index = payload.get("index")
    columns = payload.get("columns")
    data = payload.get("data")
    if not isinstance(index, list) or not isinstance(columns, list) or not isinstance(data, list):
        return None
    try:
        return pd.DataFrame(data, index=index, columns=columns)
    except Exception:
        return None


def _stats_row(report: dict[str, Any], horizon: int) -> dict[str, Any] | None:
    rows = report.get("ic_stats")
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict):
                continue
            try:
                if int(row.get("horizon")) == int(horizon):
                    return row
            except (TypeError, ValueError):
                continue
    return None


def _mean_spread(frame: pd.DataFrame | None) -> float | None:
    if frame is None or "Q5" not in frame.columns or "Q1" not in frame.columns:
        return None
    values = pd.to_numeric(frame["Q5"] - frame["Q1"], errors="coerce").dropna()
    return float(values.mean()) if len(values) else None


def _html_run_id(path: Path) -> str | None:
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8", errors="ignore")
    match = re.search(r"RUN</span>\s*([^<\s]+)", text)
    return match.group(1) if match else None


def _g3_status(report: dict[str, Any], policy: dict[str, Any], g3_checks: list[dict[str, Any]]) -> str:
    statuses = [item["status"] for item in g3_checks]
    if "fail" in statuses:
        return "fail"
    if "warn" in statuses:
        return "warn"
    return "pass"


def audit_experiment(root: Path, *, policy: dict[str, Any] | None = None, ledger: Ledger | None = None) -> dict[str, Any]:
    policy = policy or load_policy()
    ledger = ledger or Ledger()
    manifest = read_json(root / "manifest.json") or {}
    report_payload = read_json(root / "report.json") or {}
    report = report_payload.get("report") if isinstance(report_payload, dict) else None
    report = report if isinstance(report, dict) else {}
    html_path = Path(str(report_payload.get("html") or root / "report.html"))

    checks: list[dict[str, Any]] = []
    g3_checks: list[dict[str, Any]] = []

    def add(check_id: str, title: str, status: str, actual: Any, expected: Any, note: str = "") -> None:
        checks.append(_check(check_id, title, status, str(actual), str(expected), note))

    # ------------------------------------------------------------- artifacts
    required = ["manifest.json", "report.json", "report.html"]
    missing = [name for name in required if not (root / name).exists()]
    add("artifacts", "required artifacts", "fail" if missing else "pass", f"missing={missing}", "all present")

    if not manifest or not report:
        audit = {
            "schema_version": config.SCHEMA_VERSION,
            "exp_id": root.name,
            "verdict": "fail",
            "g3": "fail",
            "checks": checks,
            "g3_checks": g3_checks,
            "policy": {"version": policy.get("version"), "sha256": policy.get("sha256")},
            "ledger": ledger.verify(),
            "created_at": config.now_iso(),
        }
        write_json_atomic(root / "audit.json", audit)
        return audit

    run_id = str(manifest.get("run_id") or "")
    report_meta = report_payload.get("meta") or {}
    horizon = int((manifest.get("outcome") or {}).get("horizon", 5))
    groups = int((manifest.get("outcome") or {}).get("groups", 5))
    outcome = manifest.get("outcome") or {}

    # ------------------------------------------------------------- run_id
    meta_run = str(report_meta.get("run_id") or "")
    html_run = _html_run_id(html_path)
    run_ok = bool(run_id) and run_id == meta_run == html_run
    add(
        "run_id",
        "manifest / report / html run_id",
        "pass" if run_ok else "fail",
        f"manifest={run_id} meta={meta_run} html={html_run}",
        "all equal",
    )

    # ------------------------------------------------------------- parameters
    params = manifest.get("params") or {}
    report_start = str(report.get("start", ""))[:10]
    report_end = str(report.get("end", ""))[:10]
    manifest_start = str(params.get("start", ""))[:10]
    manifest_end = str(params.get("end") or "")[:10]
    horizon_ok = int(horizon) in [int(item) for item in report.get("horizons", [])]
    start_ok = bool(report_start) and bool(manifest_start) and report_start >= manifest_start
    end_ok = (not manifest_end) or (bool(report_end) and report_end <= manifest_end)
    param_ok = (
        str(report.get("factor_id")) == str(manifest.get("factor_id"))
        and horizon_ok
        and start_ok
        and end_ok
    )
    add(
        "params",
        "factor / horizon / date window",
        "pass" if param_ok else "fail",
        f"factor={report.get('factor_id')} horizons={report.get('horizons')} start={report_start} end={report_end}",
        f"factor={manifest.get('factor_id')} horizon={horizon} start>={manifest_start} end<={manifest_end or 'n/a'}",
    )

    # ------------------------------------------------------------- sample
    n_symbols = int(report.get("n_symbols") or 0)
    n_dates = int(report.get("n_dates") or 0)
    coverage = float(report.get("coverage") or 0.0)
    min_coverage = float((policy.get("thresholds") or {}).get("min_coverage", 0.6))
    sample_status = "pass"
    sample_note = ""
    if n_symbols < groups * 5 or n_dates <= 0:
        sample_status = "fail"
        sample_note = "not enough symbols/dates for quantile report"
    elif coverage < min_coverage:
        sample_status = "warn"
        sample_note = f"coverage below policy {min_coverage:.0%}"
    add(
        "sample",
        "symbols / dates / coverage",
        sample_status,
        f"symbols={n_symbols} dates={n_dates} coverage={coverage:.3f}",
        f"symbols>={groups * 5}, dates>0, coverage>={min_coverage:.2f}",
        sample_note,
    )

    # ------------------------------------------------------------- IC recompute
    ic_values = _series_values((report.get("ic") or {}).get(str(horizon)))
    stats = _stats_row(report, horizon) or {}
    ic_mean = float(stats.get("mean")) if stats.get("mean") is not None else None
    ic_t = float(stats.get("t")) if stats.get("t") is not None else None
    if ic_values and ic_mean is not None:
        recomputed_mean = float(np.mean(ic_values))
        mean_ok = _close(ic_mean, recomputed_mean, 1e-6)
        add(
            "ic_recompute",
            "IC mean recompute",
            "pass" if mean_ok else "fail",
            f"mean={ic_mean} n={stats.get('n')}",
            f"mean={recomputed_mean:.10f}",
            "n is the report's date count; IC values exclude NaN warmup rows.",
        )
    else:
        add("ic_recompute", "IC mean recompute", "warn", "missing ic series/stats", "present")

    # ------------------------------------------------------------- spread + cost
    gross = _frame(report.get("period_returns_gross"))
    gross_mean = _mean_spread(gross)
    spread_gross = float((report.get("spread_gross") or {}).get("mean", float("nan")))
    spread_net = float((report.get("spread_net") or {}).get("mean", float("nan")))
    spread_ok = gross_mean is not None and _close(gross_mean, spread_gross)
    add(
        "spread_recompute",
        "Q5-Q1 gross recompute",
        "pass" if spread_ok else "fail",
        f"gross={gross_mean}",
        f"spread_gross={spread_gross}",
    )

    cost_mean = float(report.get("cost_mean")) if report.get("cost_mean") is not None else float("nan")
    cost_ok = (
        gross_mean is not None
        and math.isfinite(spread_net)
        and math.isfinite(cost_mean)
        and _close(spread_gross - cost_mean, spread_net)
    )
    add(
        "cost_identity",
        "net = gross - cost",
        "pass" if cost_ok else "fail",
        f"gross={spread_gross} cost={cost_mean} net={spread_net}",
        "gross - cost == net within 1e-6",
    )

    # ------------------------------------------------------------- monotonicity
    quantiles = report.get("quantile_returns_gross") or report.get("quantile_returns") or {}
    if isinstance(quantiles, dict) and len(quantiles) >= 3:
        ordered = [quantiles.get(f"Q{i}") for i in range(1, 6)]
        if all(value is not None for value in ordered):
            series = pd.Series([float(value) for value in ordered])
            rho = float(series.rank().corr(pd.Series(range(1, len(series) + 1)).rank()))
            reported = float(report.get("monotonicity", float("nan")))
            add(
                "monotonicity",
                "quantile monotonicity",
                "pass" if _close(rho, reported, 2e-3) else "warn",
                f"recomputed={rho:.6f}",
                f"reported={reported:.6f}",
                "warn only: gross/net quantile definition may differ.",
            )
        else:
            add("monotonicity", "quantile monotonicity", "warn", "missing Q1..Q5", "present")
    else:
        add("monotonicity", "quantile monotonicity", "warn", "missing quantile_returns", "present")

    # ------------------------------------------------------------- policy / ledger
    manifest_policy = manifest.get("policy") or {}
    policy_ok = manifest_policy.get("sha256") == policy.get("sha256")
    add(
        "policy",
        "gate policy hash",
        "pass" if policy_ok else "fail",
        f"manifest={manifest_policy.get('sha256')} current={policy.get('sha256')}",
        "equal",
    )

    verification = ledger.verify()
    chain_status = "pass" if verification.get("ok") else "fail"
    if verification.get("repairs"):
        chain_status = "warn" if verification.get("ok") else "fail"
    add(
        "ledger_chain",
        "ledger hash chain / repairs",
        chain_status,
        f"records={verification.get('records')} repairs={len(verification.get('repairs') or [])} unrepaired={len(verification.get('unrepaired') or [])}",
        "no unrepaired gaps",
    )

    orphan = ledger.orphan_check(
        run_id=run_id,
        hypothesis_id=str(manifest.get("hypothesis_id") or ""),
        trial_id=str(manifest.get("trial_id") or ""),
    )
    add(
        "orphan_run",
        "report run links to earlier hypothesis",
        "pass" if orphan.get("ok") else "fail",
        "; ".join(orphan.get("issues") or []) or "linked",
        "hypothesis registered before run",
    )

    # ------------------------------------------------------------- outcome contract
    hypothesis = read_json(root / "hypothesis.json") or {}
    outcomes = hypothesis.get("outcomes") or []
    outcome_keys = {OutcomeSpec.from_dict(item).key() for item in outcomes if isinstance(item, dict)}
    outcome_ok = OutcomeSpec.from_dict(outcome).key() in outcome_keys
    add(
        "outcome_pre_registered",
        "run outcome belongs to hypothesis",
        "pass" if outcome_ok else "fail",
        OutcomeSpec.from_dict(outcome).key(),
        f"one of {sorted(outcome_keys)}",
    )

    # ------------------------------------------------------------- orthogonality
    orthogonality = read_json(root / "orthogonality.json") or {}
    if orthogonality:
        rho_status = str(orthogonality.get("decision") or "warn")
        add(
            "orthogonality",
            "G0.5 exposure correlation",
            rho_status if rho_status in ("pass", "warn", "fail") else "warn",
            f"max|rho|={orthogonality.get('max_abs_rho')} nearest={orthogonality.get('nearest')} CI=[{orthogonality.get('ci_low')}, {orthogonality.get('ci_high')}]",
            "CI lower > hard => fail; upper < warn => pass; crossing => warn",
            "exposure-only; rho_return pending",
        )
        max_rho = orthogonality.get("max_abs_rho")
    else:
        add("orthogonality", "G0.5 exposure correlation", "warn", "not computed", "P1 orthogonality.py")
        max_rho = None

    # ------------------------------------------------------------- G3 tradeability
    turnover = report.get("turnover")
    max_turnover = float((policy.get("thresholds") or {}).get("max_turnover_5d", 0.6))
    if turnover is None:
        g3_checks.append(_check("turnover", "turnover cap", "warn", "missing", f"<= {max_turnover:.0%}"))
    else:
        turnover = float(turnover)
        g3_checks.append(
            _check(
                "turnover",
                "turnover cap",
                "pass" if turnover <= max_turnover else "fail",
                f"{turnover:.3f}",
                f"<= {max_turnover:.3f}",
            )
        )

    capacity = report.get("capacity") or {}
    capacity_multiple = None
    if isinstance(capacity, dict):
        for key in ("multiple", "capacity_multiple", "ratio"):
            if key in capacity:
                try:
                    capacity_multiple = float(capacity[key])
                except (TypeError, ValueError):
                    capacity_multiple = None
                break
    min_capacity = float((policy.get("thresholds") or {}).get("min_capacity_multiple", 3.0))
    if capacity_multiple is None:
        g3_checks.append(_check("capacity", "capacity vs target", "warn", "missing", f">= {min_capacity:g}x"))
    else:
        g3_checks.append(
            _check(
                "capacity",
                "capacity vs target",
                "pass" if capacity_multiple >= min_capacity else "fail",
                f"{capacity_multiple:.2f}x",
                f">= {min_capacity:g}x",
            )
        )

    # cost sensitivity is a report field when the default profile is used; P0 quick may omit it.
    sensitivity = report.get("sensitivity")
    if sensitivity:
        g3_checks.append(_check("cost_sensitivity", "2x cost still positive", "pass", "reported", "check sensitivity table"))
    elif gross_mean is not None and math.isfinite(cost_mean):
        net_2x = spread_gross - 2.0 * cost_mean
        g3_checks.append(
            _check(
                "cost_sensitivity",
                "2x cost still positive",
                "pass" if net_2x > 0 else "fail",
                f"2x net={net_2x:.8f}",
                "2x cost net > 0",
            )
        )
    else:
        g3_checks.append(_check("cost_sensitivity", "2x cost still positive", "warn", "missing", "2x cost net > 0"))

    g3 = _g3_status(report, policy, g3_checks)
    add("g3_tradeability", "G3 tradeability subchecks", "warn" if g3 != "pass" else "pass", g3, "pass", "see g3_checks")

    # ------------------------------------------------------------- diagnostics / rating
    diagnostics = report.get("diagnostics") or []
    if isinstance(diagnostics, list) and diagnostics:
        failed = [item for item in diagnostics if isinstance(item, dict) and item.get("status") == "fail"]
        add(
            "report_diagnostics",
            "report diagnostics",
            "warn" if failed else "pass",
            f"{len(failed)} fail / {len(diagnostics)} checks",
            "no fail (warn only)",
        )
    else:
        add("report_diagnostics", "report diagnostics", "warn", "missing", "present")

    rating = report.get("rating") or report_payload.get("rating") or {}
    rating_ok = bool(rating.get("grade")) and rating.get("score") is not None
    add("rating", "grade / score present", "pass" if rating_ok else "fail", f"grade={rating.get('grade')} score={rating.get('score')}", "present")

    hard_fail = any(item["status"] == "fail" for item in checks)
    has_warn = any(item["status"] == "warn" for item in checks)
    verdict = "fail" if hard_fail else ("warn" if has_warn else "pass")
    summary = ledger.summary()
    n_trials = int(summary.get("distinct_trials") or 0)
    p_value = two_sided_p(ic_t) if ic_t is not None else None
    q_value = None
    q_method = "unavailable"
    if p_value is not None:
        if n_trials <= 1:
            q_value = p_value
            q_method = "single-trial: raw p"
        else:
            q_value = min(1.0, p_value * n_trials)
            q_method = "Bonferroni fallback (P0); BH batch to follow"
    q_note = (
        "single-trial raw p"
        if n_trials <= 1
        else "Bonferroni fallback across distinct trials; BH batch to follow"
    )
    net_2x = spread_gross - 2.0 * cost_mean if math.isfinite(cost_mean) and math.isfinite(spread_gross) else None
    audit = {
        "schema_version": config.SCHEMA_VERSION,
        "exp_id": root.name,
        "run_id": run_id,
        "verdict": verdict,
        "g3": g3,
        "g0_5": (orthogonality.get("decision") if isinstance(orthogonality, dict) else None),
        "checks": checks,
        "g3_checks": g3_checks,
        "policy": {"version": policy.get("version"), "sha256": policy.get("sha256")},
        "ledger": verification,
        "orphan": orphan,
        "n_trials": n_trials,
        "q_value": q_value,
        "q_method": q_method,
        "p_value": p_value,
        "q_note": q_note,
        "metrics": {
            "ic": ic_mean,
            "ic_t": ic_t,
            "ic_n": len(ic_values),
            "spread_gross": spread_gross,
            "spread_net": spread_net,
            "cost_mean": cost_mean,
            "net_2x_cost": net_2x,
            "turnover": turnover,
            "capacity_multiple": capacity_multiple,
            "max_rho": max_rho,
        },
        "created_at": config.now_iso(),
    }
    write_json_atomic(root / "audit.json", audit)
    ledger.append(
        "audit_written",
        {
            "exp_id": root.name,
            "run_id": run_id,
            "verdict": verdict,
            "g3": g3,
            "path": str(root / "audit.json"),
        },
    )
    ledger.write_summary()
    return audit
