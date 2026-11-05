"""RQAlpha 引擎 golden：指标与成交数回归。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_rqalpha_engine import _frame, _runner

GOLDEN = Path(__file__).resolve().parent / "golden" / "rqalpha"

CASES = [
    (
        "rank_mom20_2024.json",
        "strategies/models/rank_mom20.py",
        "2024-03-01",
        "2024-09-30",
    ),
    (
        "atr_trend_2024.json",
        "strategies/models/atr_trend.py",
        "2024-03-01",
        "2025-01-31",
    ),
]


@pytest.mark.parametrize("golden_name,path,start,end", CASES)
def test_metrics_match_golden(golden_name, path, start, end):
    expected = json.loads((GOLDEN / golden_name).read_text(encoding="utf-8"))
    result = _runner(_frame()).run(path, start=start, end=end)
    assert len(result.orders) == expected["orders"]
    assert len(result.trades) == expected["trades"]
    for key, value in expected["metrics"].items():
        assert result.metrics[key] == pytest.approx(value, rel=0.02, abs=0.02)
