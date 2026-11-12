"""Point-in-time universe: eligibility, no-lookahead and evaluation wiring."""

from __future__ import annotations

import json

import numpy as np
import pandas as pd

from factor_lab.alpha.compiler import AlphaContext
from factor_lab.alpha.data import AlphaDataset
from factor_lab.alpha.evaluation import evaluate_expression
from factor_lab.alpha.memory import AlphaMemory
from factor_lab.alpha.search import run_search
from factor_lab.alpha.universe import (
    UNIVERSE_POLICY_SCHEMA,
    UNIVERSE_SCHEMA,
    UniversePolicy,
    build_pit_universe,
    default_policy_path,
    load_universe_policy,
    universe_summary,
)
from factor_lab.core import config


def _context(days: int = 320, symbols: int = 30) -> AlphaContext:
    rng = np.random.default_rng(11)
    dates = pd.bdate_range("2023-01-02", periods=days)
    columns = [f"{index:06d}" for index in range(symbols)]
    rets = pd.DataFrame(rng.normal(0.0005, 0.018, (days, symbols)), index=dates, columns=columns)
    close = 10.0 * (1.0 + rets).cumprod()
    volume = pd.DataFrame(rng.lognormal(13, 0.5, (days, symbols)), index=dates, columns=columns)
    amount = volume * close
    # 000001: delisted after bar 119 (never comes back)
    close.iloc[120:, 1] = np.nan
    volume.iloc[120:, 1] = np.nan
    # 000002: one-day suspension at bar 100
    close.iloc[100, 2] = np.nan
    volume.iloc[100, 2] = np.nan
    # 000003: listed at bar 90 (no data before)
    close.iloc[:90, 3] = np.nan
    volume.iloc[:90, 3] = np.nan
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


def test_policy_defaults_to_repo_source_of_truth() -> None:
    path = default_policy_path()
    assert path.exists()
    assert not str(path.resolve()).startswith(str(config.LAB_ROOT.resolve()))
    policy = load_universe_policy()
    assert policy.enabled is True
    assert policy.min_history_days == 60
    assert policy.to_dict()["schema"] == UNIVERSE_POLICY_SCHEMA


def test_policy_json_roundtrip(tmp_path) -> None:
    target = tmp_path / "universe.json"
    target.write_text(
        json.dumps(
            {
                "schema": UNIVERSE_POLICY_SCHEMA,
                "name": "pit_test",
                "enabled": False,
                "min_history_days": 20,
                "min_close": 1.0,
                "require_positive_volume": False,
            }
        ),
        encoding="utf-8",
    )
    policy = load_universe_policy(target)
    assert policy.name == "pit_test"
    assert policy.enabled is False
    assert policy.min_history_days == 20
    assert policy.min_close == 1.0
    assert policy.require_positive_volume is False


def test_pit_eligibility_delisting_suspension_and_listing() -> None:
    context = _context()
    policy = UniversePolicy(min_history_days=60)
    mask = build_pit_universe(context, policy)

    # 正常标的：前 59 个 bar 不满足 60 日历史，第 60 个 bar 起才合格
    assert not bool(mask.iloc[58]["000000"])
    assert bool(mask.iloc[59]["000000"])

    # 退市：最后有效 bar 之后立即退出 universe，且不会被前向填充
    assert bool(mask.iloc[119]["000001"])
    assert not bool(mask.iloc[120]["000001"])
    assert not bool(mask.iloc[-1]["000001"])

    # 单日停牌：停牌日剔除，停牌后需要重新攒满 60 日历史才恢复
    assert not bool(mask.iloc[100]["000002"])
    assert not bool(mask.iloc[159]["000002"])
    assert bool(mask.iloc[160]["000002"])

    # 次新股：上市后攒满 60 日历史才进入 universe
    assert not bool(mask.iloc[148]["000003"])
    assert bool(mask.iloc[149]["000003"])

    summary = universe_summary(mask, policy)
    assert summary is not None
    assert summary["schema"] == UNIVERSE_SCHEMA
    assert summary["policy"]["min_history_days"] == 60
    assert summary["n_symbols"] == context.close.shape[1]
    assert 0 < summary["avg_size"] < context.close.shape[1]
    assert 0 < summary["eligible_fraction"] < 1


def test_pit_universe_has_no_lookahead() -> None:
    context = _context()
    mask = build_pit_universe(context, UniversePolicy(min_history_days=60))

    # 篡改未来数据（第 150 个 bar 之后）不得影响更早日期的合格判定
    future_context = _context()
    future_context.close.iloc[150:, 0] = 999.0
    future_context.fields["volume"].iloc[150:, 0] = 1.0
    changed = build_pit_universe(future_context, UniversePolicy(min_history_days=60))

    cutoff = context.close.index[149]
    assert mask.loc[:cutoff].equals(changed.loc[:cutoff])


def test_evaluate_expression_coverage_is_universe_relative() -> None:
    context = _context()
    policy = UniversePolicy(min_history_days=60)
    universe = build_pit_universe(context, policy)

    baseline = evaluate_expression("rank(close)", context, horizon=5, cost_bps=0.0)
    pit = evaluate_expression("rank(close)", context, horizon=5, cost_bps=0.0, universe=universe)

    assert baseline["universe_size_avg"] is None
    assert pit["universe_size_avg"] is not None
    assert pit["universe_size_avg"] < context.close.shape[1]
    # 固定池会把退市/次新/停牌的空白算进 coverage；PIT universe 只看合格 cell
    assert pit["coverage"] > baseline["coverage"]


def test_run_search_records_pit_universe(tmp_path, monkeypatch) -> None:
    import factor_lab.alpha.trials as trials_mod

    monkeypatch.setattr(trials_mod, "alpha_dir", lambda: tmp_path)
    context = _context()
    dataset = _dataset(context)
    policy = UniversePolicy(min_history_days=60)
    universe = build_pit_universe(context, policy)

    result = run_search(
        dataset,
        budget=3,
        horizon=5,
        holdout=None,
        seed=5,
        out_path=str(tmp_path / "search.json"),
        memory=AlphaMemory(tmp_path / "memory"),
        factor_library_root=tmp_path / "factors",
        universe=universe,
        universe_policy=policy,
    )

    assert result["universe"]["schema"] == UNIVERSE_SCHEMA
    assert result["universe"]["policy"]["name"] == "pit_v1"
    assert result["n_evaluated"] >= 1
    assert result["top"]
    assert result["top"][0]["metrics"]["universe_size_avg"] is not None
