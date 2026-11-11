"""End-to-end research cycle: Runner -> Runtime -> Evaluator -> Memory -> Campaign.

This is the deterministic L0 execution path used by the research engine.  It
does not call an LLM; a PathFinder/FactorBuilder may choose the artifact and
campaign, but the evidence path is fixed.
"""

from __future__ import annotations

from typing import Any

from factor_lab.alpha.data import AlphaDataset
from factor_lab.alpha.memory import AlphaMemory
from factor_lab.alpha.research_runtime import edge_card_from_result, explain_result, run_research
from factor_lab.alpha.runner import FactorArtifact, prepare_research_request
from factor_lab.alpha.supervisor import Supervisor


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
) -> dict[str, Any]:
    supervisor = supervisor or Supervisor()
    memory = memory or AlphaMemory()
    request = prepare_research_request(artifact, dataset)
    result = run_research(
        request["expression"],
        dataset,
        factor_ref=request["factor_ref"],
        horizon=horizon,
        cost_bps=cost_bps,
    )
    evaluation = explain_result(result)
    card_status = "research" if evaluation.get("verdict") == "pass" else "killed"
    memory.record_attempt(
        request["expression"],
        status="evaluated" if card_status == "research" else "failed",
        reason=None if card_status == "research" else "; ".join(evaluation.get("reasons") or []),
        metrics=result.get("metrics") or {},
        source="research_cycle",
        generation=0,
        horizon=horizon,
        cost_bps=cost_bps,
        search_id=f"cycle:{campaign_id}",
    )
    card = edge_card_from_result(result, status=card_status, source="research_cycle")
    card["factor_ref"] = request["factor_ref"]
    card["data_manifest"] = request["data_bundle"].get("data_manifest")
    memory.record_edge_card(card)
    memory.finalize()
    round_record = supervisor.round(
        campaign_id,
        {
            "research_question": f"evaluate {artifact.factor_id} {artifact.version}",
            "factor_refs": [request["factor_ref"]],
            "data_manifest": request["data_bundle"].get("data_manifest"),
            "research_result": {"schema": result["schema"], "metrics": result["metrics"], "evaluation": evaluation},
            "decision": decision,
            "cost": 0.0,
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
        "memory": memory.summary(),
    }
