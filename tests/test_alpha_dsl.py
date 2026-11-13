from __future__ import annotations

import numpy as np
import pandas as pd

from factor_lab.alpha.compiler import AlphaContext, compile_expression
from factor_lab.alpha.expression import parse_expression
from factor_lab.alpha.evaluation import evaluate_factor, evaluate_expression
from factor_lab.alpha.red_team import red_team_review
from factor_lab.alpha.memory import AlphaMemory
from factor_lab.alpha.search import run_search
from factor_lab.alpha.validation import validate
from factor_lab.alpha.data import AlphaDataset, default_holdout


def _context(days: int = 200, symbols: int = 30) -> AlphaContext:
    rng = np.random.default_rng(3)
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


def test_parse_and_render() -> None:
    expression = parse_expression("rank(ts_corr(rank(close), rank(amount), 20))")
    assert expression.fields == ["amount", "close"]
    assert expression.operators == ["rank", "ts_corr"]
    assert str(expression) == "rank(ts_corr(rank(close), rank(amount), 20))"


def test_validation_modes_and_errors() -> None:
    context = _context()
    panel = validate("rank(ts_corr(rank(close), rank(volume), 10))", known_fields=context.field_names)
    assert panel.ok
    assert panel.mode == "panel"
    bad = validate("rank(ts_mean(close, -3))", known_fields=context.field_names)
    assert not bad.ok
    unknown = validate("rank(ts_mean(not_a_field, 5))", known_fields=context.field_names)
    assert not unknown.ok


def test_compiler_shapes_and_no_lookahead() -> None:
    context = _context()
    factor, report = compile_expression("rank(ts_corr(rank(close), rank(volume), 20))", context)
    assert factor.shape == context.close.shape
    assert report.ok
    assert np.isfinite(factor.iloc[30:]).any().any()
    # Future shifts are not part of the operator registry.
    assert "shift" not in report.operators


def test_evaluator_and_red_team() -> None:
    context = _context()
    forward = context.close.shift(-5) / context.close - 1.0
    metrics = evaluate_factor(forward, context.close, horizon=5, cost_bps=20.0)
    assert metrics["n_dates"] > 5
    assert metrics["ic_mean"] > 0.5
    assert metrics["capacity_target_aum"] == 50_000_000.0
    assert "winsorized_factor_net_spread" in metrics
    assert "winsorized_net_spread" not in metrics
    assert "trimmed_net_spread" not in metrics
    assert metrics["extras"]["trimmed_net_spread_experimental"] is True
    review = red_team_review(forward, context.close, metrics, horizon=5, permutations=8)
    assert "checks" in review
    assert review["permutation_p"] <= 1.0


def test_expression_evaluator() -> None:
    context = _context()
    result = evaluate_expression("rank(ts_corr(rank(close), rank(volume), 10))", context, horizon=5, cost_bps=20.0)
    assert result["expression"].startswith("rank(")
    assert result["validation"]["ok"]
    assert "net_spread" in result


def test_holdout_and_search(tmp_path, monkeypatch) -> None:
    context = _context()
    dataset = AlphaDataset(
        context=context,
        codes=list(context.close.columns),
        start=str(context.close.index.min().date()),
        end=str(context.close.index.max().date()),
        warmup_days=10,
        data_manifest={"fields": context.field_names, "codes": len(context.close.columns)},
    )
    import factor_lab.alpha.trials as trials_mod

    monkeypatch.setattr(trials_mod, "alpha_dir", lambda: tmp_path)
    window = default_holdout("2026-11-01")
    result = run_search(dataset, budget=6, horizon=5, holdout=None, seed=2, out_path=str(tmp_path / "search.json"), memory=AlphaMemory(tmp_path / "memory"), factor_library_root=tmp_path / "factors")
    assert result["n_evaluated"] >= 1
    assert result["top"]
    assert result["fdr"]["n_trials"] == result["n_evaluated"]
    assert window.locked


def test_next_open_metric_and_holdout_guard() -> None:
    from factor_lab.alpha.data import HoldoutWindow

    context = _context()
    forward_close = context.close.shift(-5) / context.close - 1.0
    metrics_close = evaluate_factor(forward_close, context.close, horizon=5, cost_bps=0.0)
    metrics_open = evaluate_factor(forward_close, context.close, horizon=5, cost_bps=0.0, open_=context.fields["open"])
    assert metrics_close["entry_rule"] == "same_close"
    assert metrics_open["entry_rule"] == "next_open"
    try:
        HoldoutWindow("2022-10-01", "2025-10-31", "2025-01-01", "2026-11-01", locked=True)
    except ValueError:
        pass
    else:
        raise AssertionError("overlapping locked holdout must fail")


def test_where_scalar_and_window_validation() -> None:
    from factor_lab.alpha.compiler import compile_expression

    context = _context()
    condition = context.fields["close"] > context.close.mean().mean()
    factor = compile_expression("where(sign(close), 1, close)", context)[0]
    assert factor.notna().any().any()
    report = validate("ts_mean(close, 1)", known_fields=context.field_names)
    assert report.ok
