"""End-to-end research cycle: Runner -> Runtime -> Evaluator -> Memory -> Campaign.

This is the deterministic L0 execution path used by the research engine.  It
does not call an LLM; a PathFinder/FactorBuilder may choose the artifact and
campaign, but the evidence path is fixed.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from factor_lab.alpha.data import AlphaDataset
from factor_lab.alpha.memory import AlphaMemory
from factor_lab.alpha.research_runtime import edge_card_from_result, explain_result, run_research
from factor_lab.alpha.runner import FactorArtifact, prepare_research_request
from factor_lab.alpha.supervisor import Supervisor
from factor_lab.alpha.universe import UniversePolicy, universe_summary


#: Tri-state verdict -> Edge Card status / memory attempt status.
VERDICT_TO_CARD = {"pass": "research", "fail": "killed", "insufficient_evidence": "parked"}
VERDICT_TO_ATTEMPT = {
    "pass": "evaluated",
    "fail": "failed",
    "insufficient_evidence": "insufficient_evidence",
}


def card_status_for_verdict(verdict: str) -> str:
    return VERDICT_TO_CARD.get(str(verdict), "parked")


def run_research_cycle(
    campaign_id: str,
    artifact: FactorArtifact,
    dataset: AlphaDataset,
    *,
    horizon: int = 5,
    cost_bps: float = 20.0,
    supervisor: Supervisor | None = None,
    memory: AlphaMemory | None = None,
    decision: str = "continue",
    universe: pd.DataFrame | None = None,
    universe_policy: UniversePolicy | None = None,
    pre_registered: bool = False,
    protocol_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    supervisor = supervisor or Supervisor()
    memory = memory or AlphaMemory()
    request = prepare_research_request(artifact, dataset)
    universe_meta = universe_summary(universe, universe_policy)
    result = run_research(
        request["expression"],
        dataset,
        factor_ref=request["factor_ref"],
        horizon=horizon,
        cost_bps=cost_bps,
        universe=universe,
    )
    if universe_meta is not None:
        result["universe"] = universe_meta
    evaluation = explain_result(result)
    verdict = str(evaluation.get("verdict") or "insufficient_evidence")
    card_status = card_status_for_verdict(verdict)
    attempt_status = VERDICT_TO_ATTEMPT[verdict]
    memory.record_attempt(
        request["expression"],
        status=attempt_status,
        reason=None if verdict == "pass" else "; ".join(evaluation.get("reasons") or []),
        metrics=result.get("metrics") or {},
        source="research_cycle",
        generation=0,
        horizon=horizon,
        cost_bps=cost_bps,
        search_id=f"cycle:{campaign_id}",
        campaign_id=campaign_id,
        family_id=artifact.family,
        pre_registered=bool(pre_registered),
    )
    card = edge_card_from_result(result, status=card_status, source="research_cycle")
    card["factor_ref"] = request["factor_ref"]
    card["data_manifest"] = request["data_bundle"].get("data_manifest")
    if universe_meta is not None:
        card["universe"] = universe_meta
        request["data_bundle"]["masks"]["universe"] = {
            "schema": universe_meta["schema"],
            "policy": universe_meta["policy"],
            "avg_size": universe_meta["avg_size"],
            "eligible_fraction": universe_meta["eligible_fraction"],
        }
    memory.record_edge_card(card)
    memory.finalize()
    round_record = supervisor.round(
        campaign_id,
        {
            "research_question": f"evaluate {artifact.factor_id} {artifact.version}",
            "factor_refs": [request["factor_ref"]],
            "data_manifest": request["data_bundle"].get("data_manifest"),
            "research_result": {
                "schema": result["schema"],
                "metrics": result["metrics"],
                "evaluation": evaluation,
                "universe": universe_meta,
            },
            "decision": decision,
            "cost": 0.0,
            "agents": list((protocol_context or {}).get("agents") or []),
            "team_tasks": list((protocol_context or {}).get("team_tasks") or []),
            "protocol": (protocol_context or {}).get("protocol"),
        },
    )
    return {
        "schema": "factor_lab.alpha.research_cycle.v1",
        "campaign_id": campaign_id,
        "factor_ref": request["factor_ref"],
        "data_bundle": request["data_bundle"],
        "research_result": result,
        "evaluation": evaluation,
        "edge_card": card,
        "round": round_record,
        "universe": universe_meta,
        "memory": memory.summary(),
    }
