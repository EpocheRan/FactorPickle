"""Phase 1 multi-cycle orchestration with Scout / Director decision records.

The Scout/Director here are deterministic adapters: they prove the cycle
contract and expose the exact hand-off points for the DSH workflow agents.
They do not pretend to be LLM agents.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from . import config
from .cycle import _existing_factors, _select_specs, run_cycle
from .cycle_ideas import CYCLE_SPECS
from .dashboard import render_dashboard
from .store import read_json, write_json_atomic


def _fresh_specs() -> list:
    existing = _existing_factors()
    return [spec for spec in CYCLE_SPECS if spec.factor_id not in existing]


def _cycle_family(index: int) -> str:
    return f"F-phase1-{config.today_tag()}-c{index}"


def _write_scout_plan(index: int, family: str, specs: list) -> dict[str, Any]:
    payload = {
        "schema_version": "factor_lab.scout_plan.v1",
        "cycle": index,
        "family_id": family,
        "agent": "Scout (deterministic Phase 1 adapter)",
        "method": "fresh-factor round-robin by bet; no reuse across cycles",
        "factors": [
            {"factor_id": spec.factor_id, "bet": spec.bet, "direction": spec.direction}
            for spec in specs
        ],
        "created_at": config.now_iso(),
    }
    write_json_atomic(config.PHASE1_DIR / f"scout_c{index}.json", payload)
    return payload


def _cycle_audits(family: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for root in sorted(config.EXPERIMENTS_DIR.iterdir()):
        manifest = read_json(root / "manifest.json") or {}
        if manifest.get("family_id") != family:
            continue
        audit = read_json(root / "audit.json")
        brief = read_json(root / "decision_brief.json") or {}
        if isinstance(audit, dict):
            rows.append(
                {
                    "exp_id": root.name,
                    "factor_id": manifest.get("factor_id"),
                    "bet": brief.get("bet"),
                    "audit": audit,
                    "brief": brief,
                }
            )
    return rows


def _write_director_memo(index: int, family: str, cycle_result: dict[str, Any]) -> dict[str, Any]:
    rows = _cycle_audits(family)
    decisions: dict[str, int] = {}
    g3: dict[str, int] = {}
    g05: dict[str, int] = {}
    for row in rows:
        audit = row["audit"]
        rec = str(row["brief"].get("recommend") or "pending")
        decisions[rec] = decisions.get(rec, 0) + 1
        g3[str(audit.get("g3"))] = g3.get(str(audit.get("g3")), 0) + 1
        g05[str(audit.get("g0_5"))] = g05.get(str(audit.get("g0_5")), 0) + 1
    best = max(
        rows,
        key=lambda row: float((row["audit"].get("metrics") or {}).get("spread_net") or -9),
        default=None,
    )
    best_bet = best.get("bet") if best else None
    memo = {
        "schema_version": "factor_lab.director_memo.v1",
        "cycle": index,
        "family_id": family,
        "agent": "Director (deterministic Phase 1 adapter)",
        "cycle_status": cycle_result.get("status"),
        "factors": len(rows),
        "decisions": decisions,
        "g3": g3,
        "g0_5": g05,
        "best": {
            "exp_id": best.get("exp_id") if best else None,
            "factor_id": best.get("factor_id") if best else None,
            "bet": best_bet,
            "net": (best["audit"].get("metrics") or {}).get("spread_net") if best else None,
            "q": best["audit"].get("q_value") if best else None,
        },
        "next_step": (
            f"continue {best_bet}" if best_bet else "open a new data source"
        ),
        "note": "Phase 1 adapter records the hand-off; replace with DSH Scout/Director agents in Phase 2.",
        "created_at": config.now_iso(),
    }
    write_json_atomic(config.PHASE1_DIR / f"director_c{index}.json", memo)
    write_json_atomic(
        config.LAB_ROOT / "research_queue.json",
        {
            "schema_version": "factor_lab.research_queue.v1",
            "cycle": index,
            "family_id": family,
            "next_bet": best_bet,
            "next_step": memo["next_step"],
            "updated_at": config.now_iso(),
        },
    )
    return memo


def _decision(row: dict[str, Any]) -> str:
    return str(row["brief"].get("recommend") or "pending")


def build_phase1_report() -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for root in sorted(config.EXPERIMENTS_DIR.iterdir()):
        manifest = read_json(root / "manifest.json") or {}
        family = str(manifest.get("family_id") or "")
        if not family.startswith("F-phase1-"):
            continue
        audit = read_json(root / "audit.json") or {}
        brief = read_json(root / "decision_brief.json") or {}
        metrics = audit.get("metrics") or {}
        rows.append(
            {
                "exp_id": root.name,
                "family": family,
                "factor_id": manifest.get("factor_id"),
                "bet": brief.get("bet"),
                "decision": _decision(row={"brief": brief}),
                "audit": audit.get("verdict"),
                "g3": audit.get("g3"),
                "g0_5": audit.get("g0_5"),
                "ic": metrics.get("ic"),
                "ic_t": metrics.get("ic_t"),
                "net": metrics.get("spread_net"),
                "rho": metrics.get("max_rho"),
                "q": audit.get("q_value"),
                "n": audit.get("n_trials"),
            }
        )

    total = len(rows)
    decisions: dict[str, int] = {}
    audits: dict[str, int] = {}
    g3: dict[str, int] = {}
    g05: dict[str, int] = {}
    for row in rows:
        for key, value in (("decisions", row["decision"]), ("audits", row["audit"]), ("g3", row["g3"]), ("g0_5", row["g0_5"])):
            target = {"decisions": decisions, "audits": audits, "g3": g3, "g0_5": g05}[key]
            target[str(value)] = target.get(str(value), 0) + 1

    g3_pass = sum(1 for row in rows if row["g3"] == "pass")
    g3_warn = sum(1 for row in rows if row["g3"] == "warn")
    g05_pass = sum(1 for row in rows if row["g0_5"] == "pass")
    g05_fail = sum(1 for row in rows if row["g0_5"] == "fail")
    q_below = sum(1 for row in rows if row["q"] is not None and float(row["q"]) < 0.1)
    high_signal_bad_spread = sum(
        1 for row in rows
        if row["q"] is not None and float(row["q"]) < 0.1 and (row["net"] or 0) <= 0
    )
    promote = sum(1 for row in rows if row["decision"] in {"promote", "promote to G4"})
    top = sorted(
        rows,
        key=lambda row: (
            row["decision"] not in {"promote", "promote to G4", "iterate"},
            row["q"] if row["q"] is not None else 9,
            -(float(row["net"]) if row["net"] is not None else -9),
        ),
    )[:8]
    if promote or g3_pass:
        verdict = "discovery"
    elif high_signal_bad_spread:
        verdict = "transmission_gap"
    elif g3_warn:
        verdict = "weak"
    else:
        verdict = "no_edge"
    report = {
        "schema_version": "factor_lab.phase1_report.v1",
        "generated_at": config.now_iso(),
        "cycles": sorted({row["family"] for row in rows}),
        "total": total,
        "decisions": decisions,
        "audits": audits,
        "g3": g3,
        "g0_5": g05,
        "rates": {
            "decision_iterate": round((decisions.get("iterate", 0) + promote) / total, 4) if total else 0,
            "g0_5_pass": round(g05_pass / total, 4) if total else 0,
            "g0_5_fail": round(g05_fail / total, 4) if total else 0,
            "g3_pass": round(g3_pass / total, 4) if total else 0,
            "g3_warn": round(g3_warn / total, 4) if total else 0,
            "q_below_0_1": round(q_below / total, 4) if total else 0,
            "high_signal_bad_spread": round(high_signal_bad_spread / total, 4) if total else 0,
        },
        "counts": {
            "g3_warn": g3_warn,
            "g05_fail": g05_fail,
            "q_below_0_1": q_below,
            "high_signal_bad_spread": high_signal_bad_spread,
            "promote": promote,
        },
        "verdict": verdict,
        "top": top,
        "rows": rows,
    }
    write_json_atomic(config.PHASE1_DIR / "phase1_report.json", report)
    csv_path = config.PHASE1_DIR / "phase1_factors.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        fields = ["exp_id", "family", "factor_id", "bet", "decision", "audit", "g3", "g0_5", "ic", "ic_t", "net", "rho", "q", "n"]
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    lines = [
        "# Phase 1 因子发现报告",
        "",
        f"- 生成时间：{report['generated_at']}",
        f"- 因子数：{total}",
        f"- cycles：{', '.join(report['cycles']) or '—'}",
        f"- 决策：{decisions}",
        f"- G0.5：{g05}",
        f"- G3：{g3}",
        f"- q<0.1：{q_below}",
        f"- 高信号但价差为负：{high_signal_bad_spread}",
        f"- 结论：**{verdict}**",
        "",
        "## Top",
        "",
        "| 实验 | 因子 | bet | 决策 | IC | net/5d | rho | q |",
        "|---|---|---|---|---:|---:|---:|---:|",
    ]
    for row in top:
        def fmt(value, digits=4):
            return "—" if value is None else f"{float(value):.{digits}f}"
        lines.append(
            f"| {row['exp_id']} | `{row['factor_id']}` | {row['bet']} | {row['decision']} | "
            f"{fmt(row['ic'])} | {fmt(row['net'], 6)} | {fmt(row['rho'], 3)} | {fmt(row['q'], 4)} |"
        )
    lines += [
        "",
        "## 口径",
        "",
        "- 每个 cycle 使用新的 family，N=4；确认 family 不计入开发 N。",
        "- G3 失败主要是 2× 成本后为负；G0.5 失败主要是与存量因子重复。",
        "- Scout/Director 是 Phase 1 确定性适配器；DSH workflow bridge 由 `phase1-agent` / `phase1-director` 消费，但本次 live Scout 超时，第 5 个 cycle 使用 fallback plan/memo，并已标注。",
    ]
    (config.PHASE1_DIR / "phase1_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report


def run_phase1(
    *,
    cycles: int = 4,
    limit: int = 4,
    profile: str = "quick",
    heartbeat_interval: int = 2,
) -> dict[str, Any]:
    config.ensure_dirs()
    config.PHASE1_DIR.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    for index in range(1, max(1, cycles) + 1):
        specs = _select_specs(_fresh_specs(), max(1, limit))
        if not specs:
            break
        family = _cycle_family(index)
        _write_scout_plan(index, family, specs)
        cycle_result = run_cycle(
            limit=limit,
            profile=profile,
            heartbeat_interval=heartbeat_interval,
            family_id=family,
            factor_ids=[spec.factor_id for spec in specs],
        )
        memo = _write_director_memo(index, family, cycle_result)
        results.append({"cycle": index, "family": family, "run": cycle_result, "memo": memo})
        write_json_atomic(config.PHASE1_DIR / "phase1_cycles.json", results)
    report = build_phase1_report()
    dashboard = render_dashboard()
    return {"cycles": results, "report": report, "dashboard": str(dashboard)}
