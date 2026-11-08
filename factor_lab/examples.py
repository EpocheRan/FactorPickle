"""Concrete P0 examples.

The first pilot is ``liquidity.amount_bias_21_252``: MA21(amount) / MA252(amount)
minus one.  It is a price/volume factor, so it can be staged without touching
production code.
"""

from __future__ import annotations

import pandas as pd

from factors.ops import price_factors

from .schemas import Hypothesis, OutcomeSpec, new_hypothesis_id


def amount_bias_21_252(frame: pd.DataFrame) -> pd.Series:
    amount = price_factors._col(frame, "amount")
    short = amount.rolling(21, min_periods=5).mean()
    long = amount.rolling(252, min_periods=60).mean()
    return price_factors._bias(short, long)


def pilot_coverage() -> list[dict[str, object]]:
    return [
        {
            "table": "ohlcv_1d",
            "field": "amount",
            "freq": "1d",
            "start": "2017-01-01",
            "coverage": 0.999,
            "pit": True,
            "defects": "none known for the P0 pool",
            "source": "questdb/tushare",
        },
        {
            "table": "ohlcv_1d",
            "field": "close",
            "freq": "1d",
            "start": "2017-01-01",
            "coverage": 0.999,
            "pit": True,
            "defects": "none known for the P0 pool",
            "source": "questdb/tushare",
        },
    ]


def pilot_hypothesis() -> Hypothesis:
    return Hypothesis(
        hypothesis_id=new_hypothesis_id("liquidity.amount_bias_21_252"),
        factor_id="liquidity.amount_bias_21_252",
        title="成交额二阶化：短期相对长期的偏离",
        mechanism=(
            "成交额是关注度与流动性的代理。短期成交额相对长期抬升，"
            "代表关注度/交易拥挤度变化，而不是绝对水平；预期与未来收益正相关。"
        ),
        expected_sign=1,
        outcomes=[
            OutcomeSpec(
                universe="pool:2022-01-04:200",
                horizon=5,
                direction=1,
                groups=5,
                cost_bps=20.0,
            )
        ],
        outcome_policy="single",
        data=["ohlcv_1d.amount", "ohlcv_1d.close"],
        kill_criteria=[
            "coverage < 0.6",
            "turnover_5d > policy.max_turnover_5d",
            "capacity < 3x target",
            "audit hard fail",
        ],
        source="backlog:20261031_factor_idea_backlog",
        staging="amount_bias_21_252",
        bet="liquidity-second-order",
        start="2019-01-01",
        end="2026-11-01",
        notes="P0 single-horse pilot; no outcome grid, no confirmation window.",
    )
