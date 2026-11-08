"""Bridge DSH Scout/Director agent outputs into the Phase 1 runner."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from . import config
from .cycle import _existing_factors, run_cycle
from .cycle_ideas import CYCLE_SPECS
from .phase1 import build_phase1_report
from .store import read_json, write_json_atomic


def _safe_plan(plan: dict[str, Any]) -> dict[str, Any]:
    selected = [str(item) for item in (plan.get("selected") or []) if item]
    by_id = {spec.factor_id: spec for spec in CYCLE_SPECS}
    existing = _existing_factors()
    valid = [item for item in selected if item in by_id and item not in existing]
    if not valid:
        raise ValueError("agent plan has no fresh, known factor ids")
    return {
        "schema_version": "factor_lab.agent_plan.v1",
        "agent": str(plan.get("agent") or "Scout (DSH workflow)"),
        "family_id": str(plan.get("family_id") or f"F-phase1-agent-{config.today_tag()}"),
        "selected": valid,
        "rationale": str(plan.get("rationale") or ""),
        "next_bet": str(plan.get("next_bet") or ""),
        "discovery_verdict": str(plan.get("discovery_verdict") or "weak"),
        "created_at": config.now_iso(),
    }


def run_agent_plan(plan_path: Path, *, profile: str = "quick", heartbeat_interval: int = 2) -> dict[str, Any]:
    raw = read_json(plan_path)
    if not isinstance(raw, dict):
        raise ValueError(f"agent plan not found or invalid: {plan_path}")
    plan = _safe_plan(raw)
    write_json_atomic(config.PHASE1_DIR / "agent_plan.json", plan)
    cycle = run_cycle(
        limit=len(plan["selected"]),
        profile=profile,
        heartbeat_interval=heartbeat_interval,
        family_id=plan["family_id"],
        factor_ids=plan["selected"],
    )
    report = build_phase1_report()
    payload = {
        "plan": plan,
        "cycle": cycle,
        "report": {
            "total": report.get("total"),
            "verdict": report.get("verdict"),
            "decisions": report.get("decisions"),
            "g3": report.get("g3"),
            "g0_5": report.get("g0_5"),
        },
    }
    write_json_atomic(config.PHASE1_DIR / "agent_cycle.json", payload)
    return payload


def apply_director_memo(memo_path: Path) -> dict[str, Any]:
    raw = read_json(memo_path)
    if not isinstance(raw, dict):
        raise ValueError(f"director memo not found or invalid: {memo_path}")
    memo = {
        "schema_version": "factor_lab.agent_memo.v1",
        "agent": str(raw.get("agent") or "Director (DSH workflow)"),
        "cycle": raw.get("cycle"),
        "family_id": raw.get("family_id"),
        "decision": raw.get("decision"),
        "rationale": raw.get("rationale"),
        "next_bet": raw.get("next_bet"),
        "next_action": raw.get("next_action"),
        "discovery_verdict": raw.get("discovery_verdict"),
        "created_at": config.now_iso(),
    }
    write_json_atomic(config.PHASE1_DIR / "agent_memo.json", memo)
    write_json_atomic(
        config.LAB_ROOT / "research_queue.json",
        {
            "schema_version": "factor_lab.research_queue.v1",
            "source": "dsh.workflow.director",
            "family_id": memo.get("family_id"),
            "next_bet": memo.get("next_bet"),
            "next_action": memo.get("next_action"),
            "updated_at": config.now_iso(),
        },
    )
    return memo


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m factor_lab.phase1_agent")
    parser.add_argument("action", choices=["run", "director"])
    parser.add_argument("--plan", default=str(config.PHASE1_DIR / "agent_plan.json"))
    parser.add_argument("--memo", default=str(config.PHASE1_DIR / "agent_memo.json"))
    parser.add_argument("--profile", default="quick")
    parser.add_argument("--heartbeat-interval", type=int, default=2)
    args = parser.parse_args(argv)
    if args.action == "run":
        result = run_agent_plan(Path(args.plan), profile=args.profile, heartbeat_interval=args.heartbeat_interval)
    else:
        result = apply_director_memo(Path(args.memo))
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
