"""Bounded multi-round PathFinder ↔ ML Engine campaign.

The LLM PathFinder is represented by a planner callback; the ML/runtime work by
an executor callback.  This module enforces the hard limits that make the loop
safe: rounds, experiments, RMB, wall clock, duplicate ideas, and no-improvement
stopping.  Every round is appended to disk.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import config
from .budget import BudgetExceeded, check_campaign, record_usage
from .store import read_json, write_json_atomic

CAMPAIGN_DIR = config.LAB_ROOT / "campaigns"


class CampaignStop(RuntimeError):
    pass


@dataclass
class CampaignPolicy:
    max_rounds: int = 5
    max_ml_experiments: int = 12
    max_wall_clock_sec: int = 3600
    per_campaign_rmb: float = 60.0
    min_improvement: float = 1e-4
    max_no_improvement_rounds: int = 2
    duplicate_penalty: str = "stop"


@dataclass
class CampaignState:
    campaign_id: str
    objective: str
    root: Path
    policy: CampaignPolicy
    started_at: float = field(default_factory=time.time)
    rounds: list[dict[str, Any]] = field(default_factory=list)
    ml_experiments: int = 0
    spent_rmb: float = 0.0
    best_score: float | None = None
    no_improvement: int = 0
    seen: set[str] = field(default_factory=set)
    stop_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "factor_lab.campaign.v1",
            "campaign_id": self.campaign_id,
            "objective": self.objective,
            "root": str(self.root),
            "policy": self.policy.__dict__,
            "elapsed_sec": round(time.time() - self.started_at, 3),
            "rounds": len(self.rounds),
            "ml_experiments": self.ml_experiments,
            "spent_rmb": round(self.spent_rmb, 6),
            "best_score": self.best_score,
            "no_improvement": self.no_improvement,
            "stop_reason": self.stop_reason,
        }


def _signature(plan: dict[str, Any]) -> str:
    raw = json.dumps(plan, sort_keys=True, ensure_ascii=False, default=str)
    return uuid.uuid5(uuid.NAMESPACE_URL, raw).hex


class CampaignRunner:
    def __init__(
        self,
        *,
        planner: Callable[[CampaignState], dict[str, Any] | None],
        executor: Callable[[dict[str, Any], CampaignState], dict[str, Any]],
        root: Path | None = None,
        policy: CampaignPolicy | None = None,
    ) -> None:
        self.planner = planner
        self.executor = executor
        self.root = Path(root or CAMPAIGN_DIR)
        self.policy = policy or CampaignPolicy()

    def _write_state(self, state: CampaignState) -> None:
        state.root.mkdir(parents=True, exist_ok=True)
        write_json_atomic(state.root / "campaign.json", state.to_dict())

    def _append_trace(self, state: CampaignState, payload: dict[str, Any]) -> None:
        state.root.mkdir(parents=True, exist_ok=True)
        line = json.dumps(
            {"at": config.now_iso(), "campaign_id": state.campaign_id, **payload},
            ensure_ascii=False,
            default=str,
        )
        with (state.root / "campaign_trace.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    def _stop(self, state: CampaignState, reason: str) -> None:
        state.stop_reason = reason
        write_json_atomic(
            state.root / "stop_decision.json",
            {
                "schema_version": "factor_lab.campaign_stop.v1",
                "campaign_id": state.campaign_id,
                "reason": reason,
                "at": config.now_iso(),
                "rounds": len(state.rounds),
                "ml_experiments": state.ml_experiments,
                "spent_rmb": round(state.spent_rmb, 6),
            },
        )
        self._write_state(state)

    def run(self, campaign_id: str | None = None, objective: str = "") -> dict[str, Any]:
        cid = campaign_id or f"CMP-{config.now_iso()[:10].replace('-', '')}-{uuid.uuid4().hex[:8]}"
        root = self.root / cid
        state = CampaignState(campaign_id=cid, objective=objective, root=root, policy=self.policy)
        root.mkdir(parents=True, exist_ok=True)
        self._write_state(state)
        self._append_trace(state, {"event": "start", "objective": objective})

        while True:
            if len(state.rounds) >= self.policy.max_rounds:
                self._stop(state, "max_rounds")
                break
            if state.ml_experiments >= self.policy.max_ml_experiments:
                self._stop(state, "max_ml_experiments")
                break
            if time.time() - state.started_at > self.policy.max_wall_clock_sec:
                self._stop(state, "max_wall_clock")
                break
            budget = check_campaign(cid, estimated_rmb=0.0)
            if not budget["ok"]:
                self._stop(state, "budget:" + "; ".join(budget["reasons"]))
                break
            if state.spent_rmb >= self.policy.per_campaign_rmb:
                self._stop(state, "campaign_rmb")
                break

            plan = self.planner(state)
            if not plan:
                self._stop(state, "planner_stop")
                break
            if str(plan.get("action", "")) == "stop":
                self._stop(state, str(plan.get("reason") or "planner_stop"))
                break
            signature = _signature(plan)
            if signature in state.seen:
                self._stop(state, "duplicate_plan")
                break
            state.seen.add(signature)

            round_no = len(state.rounds) + 1
            result = self.executor(plan, state)
            state.rounds.append({"round": round_no, "plan": plan, "result": result})
            state.ml_experiments += 1 if plan.get("action") == "ml" else 0
            cost = float(result.get("cost_rmb", 0.0) or 0.0)
            state.spent_rmb = round(state.spent_rmb + cost, 6)
            if result.get("usage"):
                try:
                    record_usage(
                        agent=str(result.get("agent", "ML Engine")),
                        tokens_in=int(result["usage"].get("tokens_in", 0)),
                        tokens_out=int(result["usage"].get("tokens_out", 0)),
                        cost_rmb=float(result["usage"].get("cost_rmb", cost)),
                        campaign_id=cid,
                    )
                except Exception:
                    pass
            score = result.get("score")
            score_value = float(score) if isinstance(score, (int, float)) else None
            if score_value is not None:
                if state.best_score is None or score_value > state.best_score + self.policy.min_improvement:
                    state.best_score = score_value
                    state.no_improvement = 0
                else:
                    state.no_improvement += 1
            round_payload = {
                "schema_version": "factor_lab.campaign_round.v1",
                "campaign_id": cid,
                "round": round_no,
                "plan": plan,
                "result": result,
                "spent_rmb": state.spent_rmb,
                "best_score": state.best_score,
                "no_improvement": state.no_improvement,
            }
            write_json_atomic(root / "rounds" / f"round-{round_no:03d}.json", round_payload)
            self._append_trace(
                state,
                {
                    "event": "round",
                    "round": round_no,
                    "plan": plan,
                    "result": result,
                    "spent_rmb": state.spent_rmb,
                    "best_score": state.best_score,
                },
            )
            self._write_state(state)
            if state.no_improvement >= self.policy.max_no_improvement_rounds:
                self._stop(state, "no_improvement")
                break
            if result.get("stop"):
                self._stop(state, str(result.get("stop_reason") or "executor_stop"))
                break

        self._append_trace(state, {"event": "finish", "stop_reason": state.stop_reason})
        write_json_atomic(
            state.root / "history_entry.json",
            {
                "schema_version": "factor_lab.history_entry.v1",
                "at": config.now_iso(),
                "cycle_id": state.campaign_id,
                "summary": f"campaign stopped: {state.stop_reason}; rounds={len(state.rounds)}",
                "tags": ["campaign", "ml"],
                "failure_modes": [state.stop_reason] if state.stop_reason else [],
                "dead_ends": [],
                "open_questions": [],
                "links": [str(state.root)],
            },
        )
        return state.to_dict()


def create_campaign(
    objective: str,
    *,
    root: Path | None = None,
    policy: CampaignPolicy | None = None,
    campaign_id: str | None = None,
) -> dict[str, Any]:
    """Create a campaign record without running an LLM.

    DSH / PathFinder consumes ``pathfinder_request.json`` and appends rounds
    through ``CampaignRunner``.  This is the portable entrypoint used by a new
    session; creating a campaign does not spend LLM budget.
    """

    base = Path(root or CAMPAIGN_DIR)
    cid = campaign_id or f"CMP-{config.now_iso()[:10].replace('-', '')}-{uuid.uuid4().hex[:8]}"
    target = base / cid
    target.mkdir(parents=True, exist_ok=True)
    pol = policy or CampaignPolicy()
    state = {
        "schema_version": "factor_lab.campaign.v1",
        "campaign_id": cid,
        "objective": objective,
        "status": "ready",
        "created_at": config.now_iso(),
        "root": str(target),
        "policy": pol.__dict__,
        "rounds": 0,
        "ml_experiments": 0,
        "spent_rmb": 0.0,
        "stop_reason": "",
    }
    write_json_atomic(target / "campaign.json", state)
    from .discovery import DiscoveryBrief, write_brief

    write_brief(
        DiscoveryBrief(
            research_question=objective,
            constraints=["non-deep-learning ML", "no direct DB access", "bounded budget"],
            known_dead_ends=[],
        ),
        target / "discovery_brief.json",
    )
    write_json_atomic(
        target / "pathfinder_request.json",
        {
            "schema_version": "factor_lab.pathfinder_request.v1",
            "campaign_id": cid,
            "objective": objective,
            "instructions": "PathFinder: read History + discovery_map; emit factor_idea or ml_idea; Supervisor enforces max rounds/budget.",
            "max_rounds": pol.max_rounds,
            "max_ml_experiments": pol.max_ml_experiments,
            "max_wall_clock_sec": pol.max_wall_clock_sec,
            "per_campaign_rmb": pol.per_campaign_rmb,
        },
    )
    return state


def load_campaign(campaign_id: str, *, root: Path | None = None) -> dict[str, Any] | None:
    path = Path(root or CAMPAIGN_DIR) / campaign_id / "campaign.json"
    raw = read_json(path)
    return raw if isinstance(raw, dict) else None


def run_ml_plan_campaign(
    dataset: Any,
    plans: list[dict[str, Any]],
    *,
    objective: str = "ML campaign",
    root: Path | None = None,
    policy: CampaignPolicy | None = None,
    campaign_id: str | None = None,
) -> dict[str, Any]:
    """Run a deterministic ML plan through the bounded campaign runtime.

    This is the offline/testable executor.  The production PathFinder produces
    plans via a DSH workflow; this function makes the loop runnable end-to-end
    without an LLM.
    """

    from .ml_engine import MLExperimentSpec, run_experiment

    queue = list(plans)

    def planner(state: CampaignState) -> dict[str, Any] | None:
        if not queue:
            return {"action": "stop", "reason": "plan_exhausted"}
        return queue.pop(0)

    def executor(plan: dict[str, Any], state: CampaignState) -> dict[str, Any]:
        if plan.get("action") != "ml":
            return {"score": None, "cost_rmb": 0.0, "error": f"unsupported action: {plan.get('action')}"}
        payload = dict(plan.get("spec") or {})
        spec = MLExperimentSpec(**payload)
        result = run_experiment(dataset, spec)
        return {
            "score": float(result.metrics.get("ic_mean", 0.0)),
            "cost_rmb": float(plan.get("estimated_rmb", 0.0) or 0.0),
            "candidate": result.candidate,
            "metrics": result.metrics,
            "reject_reasons": result.reject_reasons,
        }

    runner = CampaignRunner(
        planner=planner,
        executor=executor,
        root=root,
        policy=policy or CampaignPolicy(max_rounds=max(1, len(plans))),
    )
    result = runner.run(campaign_id=campaign_id, objective=objective)
    write_campaign_summary(result)
    return result



def write_campaign_summary(state: dict[str, Any]) -> Path:
    root = Path(str(state.get("root") or ""))
    root.mkdir(parents=True, exist_ok=True)
    path = root / "campaign_summary.json"
    write_json_atomic(path, {"schema_version": "factor_lab.campaign_summary.v1", **state})
    return path
