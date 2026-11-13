"""End-to-end research cycle: Runner -> Runtime -> Evaluator -> Memory -> Campaign.

This is the deterministic L0 execution path used by the research engine.  It
does not call an LLM; a PathFinder/FactorBuilder may choose the artifact and
campaign, but the evidence path is fixed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from factor_lab.alpha.data import AlphaDataset
from factor_lab.alpha.deployability import evaluate_long_only_expression
from factor_lab.alpha.evaluation_contract import assert_holdout_allowed, build_evaluation_contract
from factor_lab.alpha.gates import load_gate_policy, long_only_gate
from factor_lab.alpha.mandate import load_mandate
from factor_lab.alpha.memory import AlphaMemory
from factor_lab.alpha.research_runtime import edge_card_from_result, explain_result, run_research
from factor_lab.alpha.runner import FactorArtifact, prepare_research_request
from factor_lab.alpha.supervisor import Supervisor
from factor_lab.alpha.tradability import build_tradability_mask
from factor_lab.core.store import sha256_json
from factor_lab.alpha.universe import (
    UniversePolicy,
    apply_size_screen,
    evaluation_universe_summary,
    universe_summary,
)


#: Tri-state verdict -> Edge Card status / memory attempt status.
VERDICT_TO_CARD = {"pass": "research", "fail": "killed", "insufficient_evidence": "parked"}
VERDICT_TO_ATTEMPT = {
    "pass": "evaluated",
    "fail": "failed",
    "insufficient_evidence": "insufficient_evidence",
}


def _hypothesis_count(campaign_id: str) -> int:
    path = Path("output/factor_lab/alpha/campaigns") / campaign_id / "team" / "pre_registration.frozen.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return 1
    return sum(len(family.get("candidates") or []) for family in payload.get("families") or [])


def _trial_population() -> tuple[int | None, int | None]:
    try:
        from factor_lab.alpha.fdr import estimate_n_eff
        from factor_lab.alpha.trial_accounting import freeze_ledger_snapshot

        snapshot = freeze_ledger_snapshot()
        ledger = snapshot.get("trials") or []
        expressions = [str(row.get("canonical_expression") or row.get("expression") or "") for row in ledger]
        n_eff = int(estimate_n_eff(expressions).get("n_eff") or 0) if expressions else 0
        return (n_eff or None, len(ledger) or None)
    except Exception:  # noqa: BLE001
        return (None, None)


def card_status_for_verdict(verdict: str) -> str:
    return VERDICT_TO_CARD.get(str(verdict), "parked")


def _enforce_full_team_protocol(campaign_id: str) -> None:
    """Refuse to evaluate a full-protocol campaign without a fresh team audit.

    Campaigns created by the current controller default to full_team_protocol.
    The audit is deterministic and read from campaign files; it prevents the
    Lead from relabeling one team decision as several campaigns.
    """
    from factor_lab.alpha.campaign import CampaignController, CampaignError
    from factor_lab.alpha.protocol_audit import audit_campaign

    try:
        campaign = CampaignController().load(campaign_id)
    except Exception:  # noqa: BLE001
        return
    if not bool(campaign.get("full_team_protocol")):
        return
    audit = audit_campaign(campaign_id)
    if not audit.get("ok"):
        issues = "; ".join(str(item) for item in (audit.get("issues") or [])[:6])
        raise CampaignError(
            f"full-team protocol audit failed for {campaign_id}: {issues or 'see campaign-audit'}"
        )


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
    _enforce_full_team_protocol(campaign_id)
    supervisor = supervisor or Supervisor()
    memory = memory or AlphaMemory()
    mandate = load_mandate()
    request = prepare_research_request(artifact, dataset)
    universe_meta = universe_summary(universe, universe_policy)
    full_mask = universe
    if full_mask is None:
        full_mask = pd.DataFrame(True, index=dataset.context.close.index, columns=dataset.context.close.columns)
    size_screen_available = True
    try:
        size_mask = apply_size_screen(
            dataset.context,
            full_mask,
            exclude_smallest_fraction=mandate.size_screen_fraction,
        )
    except ValueError:
        # Synthetic/limited panels may not carry float_mv; keep the evidence
        # explicit rather than silently pretending a size screen was applied.
        size_mask = full_mask
        size_screen_available = False
    evaluation_universe = evaluation_universe_summary(
        universe,
        universe_policy,
        kind="custom_mask" if universe is not None else "full_panel",
        source="campaign_round",
    )
    result = run_research(
        request["expression"],
        dataset,
        factor_ref=request["factor_ref"],
        horizon=horizon,
        cost_bps=cost_bps,
        universe=universe,
        context=dataset.evaluation_context(),
    )
    if universe_meta is not None:
        result["universe"] = universe_meta
    result["evaluation_universe"] = evaluation_universe

    # Long-only is the deployment mandate; LS remains research evidence only.
    tradability = build_tradability_mask(dataset.evaluation_context())
    long_only_full = evaluate_long_only_expression(
        request["expression"],
        dataset.evaluation_context(),
        horizon=int(mandate.primary_horizon),
        cost_bps=20.0,
        start=dataset.start,
        end=dataset.end,
        universe=full_mask,
        tradability=tradability,
    )
    long_only_size = evaluate_long_only_expression(
        request["expression"],
        dataset.evaluation_context(),
        horizon=int(mandate.primary_horizon),
        cost_bps=20.0,
        start=dataset.start,
        end=dataset.end,
        universe=size_mask,
        tradability=tradability,
    )
    long_only_gate_metrics = dict(long_only_size)
    long_only_gate_metrics["coverage"] = (result.get("metrics") or {}).get("coverage")
    promotion = long_only_gate(long_only_gate_metrics, mandate, capacity_metrics=result.get("metrics"))
    result["long_only"] = {
        "schema": "factor_lab.alpha.long_only_evidence.v1",
        "mandate": mandate.to_dict(),
        "full": long_only_full,
        "size_screened": long_only_size,
        "size_screen_available": bool(size_screen_available),
        "promotion": promotion,
    }

    # The evaluation contract is part of the result, not a report-time
    # reconstruction.  It overlaps the frozen forward holdout only if the
    # policy explicitly allows confirmation.
    assert_holdout_allowed(dataset.start, dataset.end)
    from factor_lab.alpha.gates import alpha_gate_spec

    gate_spec = alpha_gate_spec()
    n_eff, n_trials = _trial_population()
    evaluation_contract = build_evaluation_contract(
        evaluation_start=str(dataset.start),
        evaluation_end=str(dataset.end),
        universe="pit_liquidity_1000_size_screen",
        size_screen=mandate.size_screen_name,
        horizon=int(horizon),
        cost=gate_spec.cost.to_dict(),
        n_eff=n_eff,
        n_trials=n_trials,
        hypothesis_count=_hypothesis_count(campaign_id),
        pre_registration_hash=None,
        data_manifest=dataset.data_manifest,
        pool_mode=mandate.pool_mode,
        price_basis=mandate.price_basis,
        tradability=mandate.tradability,
    )
    result["evaluation_contract"] = evaluation_contract
    gate_policy = load_gate_policy()
    coverage = (result.get("metrics") or {}).get("coverage")
    long_only_status = "fail"
    long_only_reasons = list(promotion.get("reasons") or [])
    if coverage is None:
        long_only_status = "insufficient_evidence"
        long_only_reasons = ["long-only coverage unavailable"]
    else:
        try:
            if float(coverage) < float(gate_policy.min_coverage):
                long_only_reasons.append(f"coverage below gate floor {float(gate_policy.min_coverage):g}")
        except (TypeError, ValueError):
            long_only_status = "insufficient_evidence"
            long_only_reasons = ["long-only coverage non-finite"]
        if not long_only_reasons and promotion.get("verdict") == "pass":
            long_only_status = "pass"
    evaluation = explain_result(
        result,
        research_evidence=mandate.research_evidence,
        long_only_status=long_only_status,
        long_only_reasons=long_only_reasons,
    )
    evaluation["promotion"] = promotion
    evaluation["long_only_evidence"] = {
        "full": {key: long_only_full.get(key) for key in ("n_dates", "long_only_net_excess_annualized", "long_only_t", "long_only_te_annualized", "turnover")},
        "size_screened": {key: long_only_size.get(key) for key in ("n_dates", "long_only_net_excess_annualized", "long_only_t", "long_only_te_annualized", "turnover")},
        "gate": promotion,
    }
    verdict = str(evaluation.get("verdict") or "insufficient_evidence")
    card_status = card_status_for_verdict(verdict)
    attempt_status = VERDICT_TO_ATTEMPT[verdict]
    memory.record_attempt(
        request["expression"],
        status=attempt_status,
        reason=None if verdict == "pass" else "; ".join(evaluation.get("reasons") or []),
        metrics={**(result.get("metrics") or {}), "long_only": result["long_only"], "cost_model": gate_spec.cost.to_dict()},
        source="research_cycle",
        generation=0,
        horizon=int(horizon),
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
    data_manifest = request["data_bundle"].get("data_manifest") or {}
    data_provenance = {
        "data_manifest_hash": sha256_json(data_manifest) if data_manifest else "",
        "data_hash": data_manifest.get("data_hash"),
        "price_basis": data_manifest.get("price_basis"),
        "evaluation_window": [dataset.start, dataset.end],
        "universe": universe_meta,
        "factor_code_hash": request["factor_ref"].get("code_hash"),
        "pre_registration_hash": (artifact.manifest or {}).get("pre_registration_hash"),
    }
    result["data_provenance"] = data_provenance

    round_record = supervisor.round(
        campaign_id,
        {
            "research_question": f"evaluate {artifact.factor_id} {artifact.version}",
            "factor_refs": [request["factor_ref"]],
            "data_manifest": request["data_bundle"].get("data_manifest"),
            "data_provenance": data_provenance,
            "research_result": {
                "schema": result["schema"],
                "metrics": result["metrics"],
                "evaluation": evaluation,
                "universe": universe_meta,
                "evaluation_universe": evaluation_universe,
                "evaluation_contract": evaluation_contract,
                "long_only": result["long_only"],
                "promotion": promotion,
            },
            "evaluation_universe": evaluation_universe,
            "evaluation_contract": evaluation_contract,
            "long_only": result["long_only"],
            "promotion": promotion,
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
        "data_provenance": data_provenance,
        "research_result": result,
        "evaluation": evaluation,
        "edge_card": card,
        "round": round_record,
        "universe": universe_meta,
        "evaluation_universe": evaluation_universe,
        "evaluation_contract": evaluation_contract,
        "long_only": result["long_only"],
        "promotion": promotion,
        "memory": memory.summary(),
    }
