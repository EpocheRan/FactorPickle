"""ML runtime supports derived-feature expressions and a real long-only book."""

from __future__ import annotations

import numpy as np
import pandas as pd

from factor_lab.alpha.compiler import AlphaContext
from factor_lab.alpha.data import AlphaDataset
from factor_lab.alpha.ml_branch import make_ml_plan, run_ml_experiment, validate_ml_features


def _context(days: int = 180, symbols: int = 20) -> AlphaContext:
    rng = np.random.default_rng(11)
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


def _dataset(context: AlphaContext) -> AlphaDataset:
    return AlphaDataset(
        context=context,
        codes=list(context.close.columns),
        start=str(context.close.index.min().date()),
        end=str(context.close.index.max().date()),
        warmup_days=40,
        data_manifest={"fields": context.field_names, "codes": len(context.close.columns)},
    )


def test_derived_feature_expressions_are_validated_and_fitted() -> None:
    context = _context()
    features = [
        "rank(div(close, volume))",
        "ts_std(returns, 5)",
        "ts_max(returns, 5)",
        "div(ts_mean(abs(returns), 10), ts_mean(amount, 10))",
    ]
    report = validate_ml_features(features, context.field_names)
    assert report["features"] == features
    dataset = _dataset(context)
    plan = make_ml_plan(
        "derived features",
        features,
        target_horizon=5,
        n_splits=2,
        embargo_days=5,
        feature_transform="cs_rank",
        target_transform="cs_rank",
        distill_mode="projection",
    )
    result = run_ml_experiment(dataset, plan)
    assert "ts_std(returns, 5)" in result["model_card"]["features"]
    assert result["data_bundle"]["features"] == features
    assert result["long_only"]["available"] is True
    assert result["long_only"]["construction"]["book"] == "top_quintile_equal_weight"
    assert result["long_only"]["metrics"]["n_dates"] > 0


def test_invalid_derived_feature_is_rejected_before_fit() -> None:
    context = _context(days=30)
    try:
        validate_ml_features(["ts_std(not_a_field, 5)"], context.field_names)
    except ValueError as exc:
        assert "invalid" in str(exc).lower() or "unknown" in str(exc).lower()
    else:  # pragma: no cover
        raise AssertionError("invalid derived feature must be rejected")
