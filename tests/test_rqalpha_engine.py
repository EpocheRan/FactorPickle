"""RQAlpha 引擎集成测试（内存数据源，不依赖 QuestDB）。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("rqalpha")

from backtest.bootstrap import build_runner
from backtest.config import BacktestSettings
from backtest.infrastructure.data.memory import MemoryDataSource
from backtest.infrastructure.engine.rqalpha_engine import RQAlphaEngine
from backtest.infrastructure.output.console import ConsoleOutput

START = "2024-03-01"
END = "2024-09-30"

SYMBOLS = ("600519", "000001", "300750", "600036", "000858", "601318")
PROFILES = {
    "600519": (100.0, 0.30, 0.0),
    "000001": (15.0, 0.10, 1.7),
    "300750": (60.0, 0.22, 3.1),
    "600036": (30.0, 0.08, 4.6),
    "000858": (80.0, 0.16, 5.9),
    "601318": (45.0, 0.05, 2.4),
}


def _frame(days: int = 320, open_shift: float = -0.005) -> pd.DataFrame:
    dates = pd.bdate_range("2024-01-02", periods=days)
    rows = []
    for symbol in SYMBOLS:
        base, trend, phase = PROFILES[symbol]
        for i, dt in enumerate(dates):
            close = base + i * trend + 1.5 * np.sin((i + phase) / 6.0)
            rows.append(
                {
                    "symbol": symbol,
                    "date": dt,
                    "open": close * (1.0 + open_shift),
                    "high": close * 1.01,
                    "low": close * 0.99,
                    "close": close,
                    "volume": 1_000_000.0,
                    "adj_factor": 1.0,
                    "hfq_close": close,
                }
            )
    return pd.DataFrame(rows)


def _runner(frame: pd.DataFrame, benchmark: pd.DataFrame | None = None, **overrides):
    settings = BacktestSettings(
        initial_cash=1_000_000,
        outputs=("console",),
        warmup_min_bars=30,
        data="memory",
        **overrides,
    )
    return build_runner(
        settings=settings,
        data_source=MemoryDataSource(frame, benchmark=benchmark),
        engine=RQAlphaEngine(),
        outputs=[ConsoleOutput()],
    )


def test_single_symbol_runs_and_reconciles():
    result = _runner(_frame()).run(
        "strategies/models/atr_trend.py", start=START, end="2025-01-31"
    )
    assert result.meta["engine"] == "rqalpha"
    assert result.meta["rqalpha_version"]
    assert not result.equity.empty
    assert result.metrics["final_equity"] > 0
    assert result.meta["ledger_reconciliation"]["max_rel_diff"] < 1e-4


def test_cross_section_runs_and_reconciles():
    result = _runner(_frame()).run(
        "strategies/models/rank_mom20.py", start=START, end=END
    )
    assert result.spec.mode == "cross"
    assert result.meta["mode"] == "cross"
    assert result.meta["top_n"] == 3
    assert not result.selections.empty
    assert not result.orders.empty
    assert set(result.orders["symbol"]) <= set(result.selections["symbol"])
    assert (result.equity["cash"] >= -1e-6).all()
    assert result.meta["ledger_reconciliation"]["max_rel_diff"] < 1e-4
    common = result.meta["ledger_reconciliation_common"]
    assert common["max_abs_diff"] < 1e-3


def test_limit_up_blocks_buy_on_fill_day():
    frame = _frame()
    target_day = pd.Timestamp("2024-03-04")
    code = "000001"
    mask = (frame["symbol"] == code) & (frame["date"] == target_day)
    prev = frame[(frame["symbol"] == code) & (frame["date"] < target_day)].iloc[-1]["close"]
    limit = round(float(prev) * 1.1, 2)
    frame.loc[mask, ["open", "high", "low", "close"]] = limit
    result = _runner(frame).run(
        "strategies/models/rank_mom20.py", start=START, end="2024-03-29"
    )
    rejected = result.execution_report.rejected
    assert not rejected.empty
    assert (rejected["reason"] == "limit_up").any()
    buys = result.orders[
        (result.orders["type"] == "buy")
        & (result.orders["symbol"] == code)
        & (pd.to_datetime(result.orders["date"]) == target_day)
    ]
    assert buys.empty


def test_run_id_stable_and_param_sensitive():
    frame = _frame()
    first = _runner(frame).run("strategies/models/atr_trend.py", start=START, end=END)
    second = _runner(frame).run("strategies/models/atr_trend.py", start=START, end=END)
    assert first.run_id == second.run_id
    changed = _runner(frame).run(
        "strategies/models/atr_trend.py",
        start=START,
        end=END,
        params={"k": 1.0},
    )
    assert changed.run_id != first.run_id


def test_benchmark_used_and_alpha_beta():
    frame = _frame()
    dates = pd.bdate_range("2024-01-02", periods=320)
    benchmark = pd.DataFrame(
        {
            "symbol": "000300",
            "date": dates,
            "close": 100.0 + np.arange(len(dates)) * 0.05 + np.sin(np.arange(len(dates)) / 5.0),
        }
    )
    result = _runner(frame, benchmark=benchmark).run(
        "strategies/models/atr_trend.py", start=START, end=END
    )
    assert result.meta["benchmark_used"] is True
    assert result.meta["benchmark_points"] > 0
    assert "beta" in result.metrics
    assert "alpha_annualized_pct" in result.metrics


def test_max_gross_exposure_caps_buy():
    result = _runner(_frame(), max_gross_exposure=0.5).run(
        "strategies/models/atr_trend.py", start=START, end=END
    )
    order = result.orders.iloc[0]
    cost = float(order["shares"]) * float(order["fill_price"])
    assert cost <= 0.5 * 1_000_000 + float(order["fill_price"]) * 100


def test_corporate_actions_approx_flag():
    frame = _frame()
    event_date = frame["date"].iloc[100]
    frame.loc[frame["date"] == event_date, "adj_factor"] = 1.1
    frame["hfq_close"] = frame["close"] * frame["adj_factor"]
    result = _runner(frame, corporate_actions="approx").run(
        "strategies/models/atr_trend.py", start=START, end=END
    )
    assert result.meta["corporate_action_approximation"] is True
    assert result.corporate_actions is not None


def test_walkforward_returns_folds():
    result = _runner(_frame()).walkforward(
        "strategies/models/atr_trend.py",
        start=START,
        end=END,
        grid={"k": [1.0, 2.0]},
        metric="sharpe",
        windows=2,
        train_size=0.6,
        lookahead=1,
    )
    assert len(result.folds) == 2
    assert result.folds_frame().shape[0] == 2
    assert all(fold.test_start <= fold.test_end for fold in result.folds)
