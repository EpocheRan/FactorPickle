"""Evaluation-window warmup and first-class evaluation-universe provenance."""

from __future__ import annotations

import numpy as np
import pandas as pd

from factor_lab.alpha.campaign import CampaignController
from factor_lab.alpha.compiler import AlphaContext
from factor_lab.alpha.data import AlphaDataset
from factor_lab.alpha.evaluation import evaluate_expression
from factor_lab.alpha.factor_library import write_factor_artifact
from factor_lab.alpha.memory import AlphaMemory
from factor_lab.alpha.pipeline import run_research_cycle
from factor_lab.alpha.supervisor import Supervisor
from factor_lab.alpha.universe import EVALUATION_UNIVERSE_SCHEMA, evaluation_universe_summary


def _context(days: int = 320, symbols: int = 20) -> AlphaContext:
    rng = np.random.default_rng(7)
    dates = pd.bdate_range("2022-01-03", periods=days)
    columns = [f"{600000 + index:06d}" for index in range(symbols)]
    rets = pd.DataFrame(rng.normal(0.0004, 0.018, (days, symbols)), index=dates, columns=columns)
    close = 10.0 * (1.0 + rets).cumprod()
    volume = pd.DataFrame(rng.lognormal(13, 0.4, (days, symbols)), index=dates, columns=columns)
    return AlphaContext.from_fields(
        {
            "close": close,
            "open": close * 1.001,
            "high": close * 1.01,
            "low": close * 0.99,
            "volume": volume,
            "amount": volume * close,
            "returns": close.pct_change(fill_method=None),
        },
        close=close,
    )


def test_segment_preserves_pre_window_history_for_evaluation() -> None:
    context = _context()
    dataset = AlphaDataset(
        context=context,
        codes=list(context.close.columns),
        start=str(context.close.index.min().date()),
        end=str(context.close.index.max().date()),
        warmup_days=120,
        data_manifest={"fields": context.field_names, "codes": len(context.close.columns)},
    )
    eval_start = context.close.index[200]
    segmented = dataset.segment(str(eval_start.date()), str(context.close.index.max().date()))
    # The split/evaluation window remains exactly the requested slice...
    assert len(segmented.context.close) == len(context.close) - 200
    assert segmented.start == str(eval_start.date())
    # ...but the evaluator still sees the pre-window history for warmup.
    assert len(segmented.evaluation_context().close) == len(context.close)

    expression = "rank(ts_mean(close, 60))"
    truncated = evaluate_expression(
        expression,
        segmented.context,
        start=segmented.start,
        end=segmented.end,
        horizon=5,
        cost_bps=20.0,
    )
    warm = evaluate_expression(
        expression,
        segmented.evaluation_context(),
        start=segmented.start,
        end=segmented.end,
        horizon=5,
        cost_bps=20.0,
    )
    assert warm["coverage"] > truncated["coverage"] + 0.2
    assert warm["n_dates"] >= truncated["n_dates"]


def test_evaluation_universe_record_is_explicit() -> None:
    context = _context(days=20)
    mask = pd.DataFrame(True, index=context.close.index, columns=context.close.columns)
    record = evaluation_universe_summary(mask, kind="custom_mask", source="test")
    assert record["schema"] == EVALUATION_UNIVERSE_SCHEMA
    assert record["kind"] == "custom_mask"
    assert record["universe"]["eligible_fraction"] == 1.0
    full = evaluation_universe_summary(None)
    assert full["kind"] == "full_panel"
    assert full["universe"] is None


def test_campaign_round_records_evaluation_universe(tmp_path) -> None:
    controller = CampaignController(tmp_path / "campaigns")
    campaign = controller.create("universe provenance", max_rounds=2)
    record = controller.record_round(
        campaign["campaign_id"],
        {
            "research_question": "q",
            "evaluation_universe": {
                "schema": EVALUATION_UNIVERSE_SCHEMA,
                "kind": "custom_mask",
                "source": "test",
                "universe": None,
            },
        },
    )
    assert record["evaluation_universe"]["schema"] == EVALUATION_UNIVERSE_SCHEMA
    loaded = controller.rounds(campaign["campaign_id"])[0]
    assert loaded["evaluation_universe"]["kind"] == "custom_mask"


def test_research_cycle_uses_history_for_warmup(tmp_path) -> None:
    context = _context()
    dataset = AlphaDataset(
        context=context,
        codes=list(context.close.columns),
        start=str(context.close.index.min().date()),
        end=str(context.close.index.max().date()),
        warmup_days=120,
        data_manifest={"fields": context.field_names, "codes": len(context.close.columns)},
    )
    eval_start = context.close.index[200]
    segmented = dataset.segment(str(eval_start.date()), str(context.close.index.max().date()))
    controller = CampaignController(tmp_path / "campaigns")
    campaign = controller.create_synthetic("warmup", max_rounds=2)
    artifact = write_factor_artifact(
        factor_id="warmup_close",
        family="test",
        expression="rank(ts_mean(close, 60))",
        spec={"data_contract": {"fields": ["close"]}},
        library_root=tmp_path / "factors",
    )
    result = run_research_cycle(
        campaign["campaign_id"],
        artifact,
        segmented,
        horizon=5,
        cost_bps=20.0,
        supervisor=Supervisor(controller),
        memory=AlphaMemory(tmp_path / "memory"),
    )
    assert result["research_result"]["metrics"]["coverage"] > 0.5
    assert result["evaluation_universe"]["schema"] == EVALUATION_UNIVERSE_SCHEMA
    assert result["round"]["evaluation_universe"]["kind"] == "full_panel"
    assert result["round"]["research_result"]["evaluation_universe"]["kind"] == "full_panel"
    assert result["long_only"]["size_screen_available"] is False
    assert result["promotion"]["schema"] == "factor_lab.alpha.long_only_gate.v1"
    assert result["round"]["promotion"]["verdict"] in {"pass", "fail", "insufficient_evidence"}


def test_canonical_replication_grid_is_reproducible() -> None:
    from factor_lab.alpha.replication import evaluate_long_only_grid, summarize_grid

    context = _context()
    mask = pd.DataFrame(True, index=context.close.index, columns=context.close.columns)
    grid = evaluate_long_only_grid(
        "rank(ts_mean(close, 20))",
        context,
        start=str(context.close.index[200].date()),
        end=str(context.close.index.max().date()),
        universe=mask,
        size_universe=mask,
        horizons=(20,),
        windows={"pre": (str(context.close.index[200].date()), str(context.close.index[280].date()))},
    )
    assert grid["schema"] == "factor_lab.alpha.replication_grid.v1"
    assert grid["long_only"]["20"]["size"]["n_dates"] >= 0
    summary = summarize_grid(grid)
    assert "20d_size" in summary["books"]
