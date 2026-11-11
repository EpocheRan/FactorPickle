from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from factor_lab.alpha.campaign import CampaignController, CampaignError
from factor_lab.alpha.compiler import AlphaContext
from factor_lab.alpha.data import AlphaDataset
from factor_lab.alpha.discovery import ingest_discovery
from factor_lab.alpha.factor_library import (
    list_factors,
    resolve_factor,
    write_factor_artifact,
)
from factor_lab.alpha.memory import AlphaMemory, make_edge_card
from factor_lab.alpha.ml_branch import make_ml_plan, run_ml_experiment
from factor_lab.alpha.research_runtime import explain_result, run_research
from factor_lab.alpha.roles import active_roles, role_contracts
from factor_lab.alpha.runner import prepare_research_request
from factor_lab.alpha.search import run_search


def _context(days: int = 180, symbols: int = 18) -> AlphaContext:
    rng = np.random.default_rng(7)
    dates = pd.bdate_range("2023-01-02", periods=days)
    columns = [f"{index:06d}" for index in range(symbols)]
    rets = pd.DataFrame(rng.normal(0.0005, 0.018, (days, symbols)), index=dates, columns=columns)
    close = 10.0 * (1.0 + rets).cumprod()
    volume = pd.DataFrame(rng.lognormal(13, 0.5, (days, symbols)), index=dates, columns=columns)
    amount = volume * close
    return AlphaContext.from_fields(
        {
            "close": close,
            "open": close * 1.001,
            "high": close * 1.01,
            "low": close * 0.99,
            "volume": volume,
            "amount": amount,
            "returns": close.pct_change(fill_method=None),
        },
        close=close,
    )


def _dataset(context: AlphaContext) -> AlphaDataset:
    return AlphaDataset(
        context=context,
        codes=list(context.close.columns),
        start=str(context.close.index.min().date()),
        end=str(context.close.index.max().date()),
        warmup_days=10,
        data_manifest={"fields": context.field_names, "codes": len(context.close.columns)},
    )


def test_memory_write_read_dedupe_and_edge_card(tmp_path) -> None:
    memory = AlphaMemory(tmp_path / "memory")
    assert memory.known_expressions() == set()
    memory.record_attempt("rank(close)", status="evaluated", source="test")
    assert "rank(close)" in memory.known_expressions()
    card = make_edge_card(
        "rank(close)",
        {"ic_mean": 0.02, "gross_spread": 0.01, "gross_t": 2.1, "net_spread": 0.005, "net_t": 1.8, "turnover": 0.2},
        status="signal_validated",
        mechanism="test",
        data_axes=["ohlcv"],
        horizon=5,
        source="test",
    )
    memory.record_edge_card(card)
    summary = memory.finalize()
    assert summary["n_attempts"] == 1
    assert summary["n_edge_cards"] == 1
    assert summary["edge_cards_by_status"]["signal_validated"] == 1
    assert memory.context_for_search()["n_known_expressions"] == 1


def test_search_reads_memory_and_skips_known_expressions(tmp_path) -> None:
    context = _context()
    dataset = _dataset(context)
    memory = AlphaMemory(tmp_path / "memory")
    first = run_search(dataset, budget=4, horizon=5, seed=3, out_path=str(tmp_path / "a.json"), memory=memory, factor_library_root=tmp_path / "factors")
    assert first["memory"]["n_attempts"] >= 1
    cards = memory.latest_edge_cards()
    assert cards
    assert cards[0]["factor_ref"]["factor_id"].startswith("search_")
    assert (tmp_path / "factors").exists()
    second = run_search(dataset, budget=4, horizon=5, seed=3, out_path=str(tmp_path / "b.json"), memory=memory, factor_library_root=tmp_path / "factors")
    assert second["n_attempted"] == 0
    assert second["n_evaluated"] == 0


def test_factor_library_is_versioned_and_immutable(tmp_path) -> None:
    root = tmp_path / "factors"
    first = write_factor_artifact(
        factor_id="accrual_quality",
        family="quality",
        expression="rank(close)",
        spec={"data_contract": {"fields": ["close"]}},
        library_root=root,
    )
    second = write_factor_artifact(
        factor_id="accrual_quality",
        family="quality",
        expression="rank(volume)",
        spec={"data_contract": {"fields": ["volume"]}},
        library_root=root,
    )
    assert first.version == "v1"
    assert second.version == "v2"
    assert (first.path / "manifest.json").exists()
    assert first.manifest["code_hash"] != second.manifest["code_hash"]
    resolved = resolve_factor("accrual_quality", family="quality", library_root=root)
    assert resolved.version == "v2"
    assert len(list_factors(library_root=root)) == 2
    with pytest.raises(FileExistsError):
        write_factor_artifact(
            factor_id="accrual_quality",
            family="quality",
            expression="rank(close)",
            spec={},
            version="v2",
            library_root=root,
        )


def test_runner_research_runtime_and_evaluator(tmp_path) -> None:
    context = _context()
    dataset = _dataset(context)
    artifact = write_factor_artifact(
        factor_id="close_rank",
        family="price",
        expression="rank(close)",
        spec={"data_contract": {"fields": ["close"]}},
        library_root=tmp_path / "factors",
    )
    request = prepare_research_request(artifact, dataset)
    assert request["factor_ref"]["factor_id"] == "close_rank"
    assert request["data_bundle"]["schema"] == "factor_lab.alpha.data_bundle.v1"
    result = run_research(request["expression"], dataset, factor_ref=request["factor_ref"], horizon=5)
    evaluation = explain_result(result)
    assert evaluation["verdict"] in {"pass", "fail"}
    assert "caveats" in evaluation


def test_campaign_round_limits_and_stop(tmp_path) -> None:
    controller = CampaignController(tmp_path / "campaigns")
    campaign = controller.create("test research", max_rounds=2, budget=1.0)
    cid = campaign["campaign_id"]
    first = controller.record_round(
        cid,
        {"research_question": "q1", "decision": "continue", "cost": 0.1},
    )
    assert first["round_number"] == 1
    second = controller.record_round(
        cid,
        {"research_question": "q2", "decision": "continue", "cost": 2.0},
    )
    assert second["round_number"] == 2
    assert controller.load(cid)["state"] == "stopped"
    assert (controller._path(cid) / "stop_decision.json").exists()
    with pytest.raises(CampaignError):
        controller.record_round(cid, {"research_question": "q3"})


def test_ml_branch_purged_experiment(tmp_path) -> None:
    context = _context()
    dataset = _dataset(context)
    plan = make_ml_plan(
        "close and volume predict forward return",
        ["close", "volume"],
        target_horizon=5,
        n_splits=3,
        embargo_days=3,
    )
    result = run_ml_experiment(dataset, plan, out_dir=tmp_path / "ml")
    assert result["schema"] == "factor_lab.alpha.ml_result.v1"
    assert result["data_bundle"]["pit"]["required"] is True
    assert result["cv"]["embargo_days"] == 5
    assert "oos_rank_ic_mean" in result["metrics"]
    assert (tmp_path / "ml" / f"{plan.plan_id}.model_card.json").exists()


def test_discovery_requires_sources(tmp_path) -> None:
    memory = AlphaMemory(tmp_path / "memory")
    with pytest.raises(ValueError):
        ingest_discovery({"leads": [{"mechanism": "x"}]}, memory=memory)
    payload = {
        "brief": {"goal": "find mechanisms"},
        "leads": [{"mechanism": "accrual"}],
        "sources": [{"url": "https://example.com/paper", "title": "paper", "published_at": "2025-01-01"}],
    }
    result = ingest_discovery(payload, memory=memory)
    assert result["discovery"]["schema"] == "factor_lab.alpha.discovery_map.v1"
    assert result["discovery"]["sources"][0]["url"].startswith("https://")


def test_role_contracts_are_five_llm_roles() -> None:
    contracts = role_contracts()
    assert contracts["n_llm_roles"] == 5
    assert set(contracts["llm_roles"]) == {
        "PathFinder",
        "Web Gateway",
        "ML Planner",
        "FactorBuilder",
        "Reader",
    }
    assert active_roles("L0")["llm_roles"] == ["PathFinder", "FactorBuilder"]
