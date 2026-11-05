"""报告层：FIFO 回合、CAPM、月度首月与 HTML 冒烟。"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from backtest.domain.models import BacktestResult, ExecutionReport, RunSpec
from backtest.reporting.alpha import capm_stats, monthly_returns, yearly_returns
from backtest.reporting.data import ReportDataBuilder
from backtest.reporting.output import HtmlReportOutput
from backtest.reporting.render import render_report
from backtest.reporting.trades import build_round_trips


def _equity(values: list[float]) -> pd.DataFrame:
    dates = pd.bdate_range("2024-01-02", periods=len(values))
    return pd.DataFrame(
        {
            "date": dates,
            "cash": [0.0] * len(values),
            "market_value": values,
            "equity": values,
            "drawdown": list(pd.Series(values) / pd.Series(values).cummax() - 1.0),
        }
    )


def _result() -> BacktestResult:
    spec = RunSpec(
        strategy_name="demo",
        strategy_path="demo.py",
        mode="single",
        symbols=("600519",),
        start=date(2024, 1, 1),
        end=date(2024, 1, 31),
        freq="d",
        initial_cash=100_000,
        benchmark="000300",
    )
    equity = _equity([100_000, 101_000, 100_500, 102_000])
    benchmark = pd.Series(
        [1000.0, 1005.0, 1002.0, 1010.0],
        index=pd.bdate_range("2024-01-02", periods=4),
        name="000300",
    )
    orders = pd.DataFrame(
        [
            {"type": "buy", "symbol": "600519", "date": "2024-01-02", "shares": 100, "fill_price": 10.0, "fees": 1.0},
            {"type": "sell", "symbol": "600519", "date": "2024-01-31", "shares": 100, "fill_price": 12.0, "fees": 2.0},
        ]
    )
    return BacktestResult(
        run_id="report123",
        spec=spec,
        metrics={"total_return_pct": 2.0, "max_drawdown_pct": -1.0},
        equity=equity,
        orders=orders,
        trades=orders.copy(),
        selections=pd.DataFrame(columns=["date", "symbol", "score", "rank", "weight"]),
        factor_meta={},
        execution_report=ExecutionReport(),
        benchmark=benchmark,
        meta={"engine": "rqalpha", "benchmark": "000300", "benchmark_used": True},
    )


def test_fifo_round_trips_split_lots():
    orders = pd.DataFrame(
        [
            {"type": "buy", "symbol": "600000", "date": "2024-01-02", "shares": 100, "fill_price": 10.0, "fees": 1.0},
            {"type": "buy", "symbol": "600000", "date": "2024-01-03", "shares": 100, "fill_price": 12.0, "fees": 1.2},
            {"type": "sell", "symbol": "600000", "date": "2024-01-10", "shares": 150, "fill_price": 15.0, "fees": 5.0},
        ]
    )
    trips = build_round_trips(orders)
    assert len(trips) == 2
    assert trips.iloc[0]["shares"] == 100
    assert trips.iloc[0]["entry_price"] == pytest.approx(10.0)
    assert trips.iloc[0]["pnl"] == pytest.approx((15 - 10) * 100 - 1.0 - 5.0 * 100 / 150)
    assert trips.iloc[1]["shares"] == 50
    assert trips.iloc[1]["entry_price"] == pytest.approx(12.0)


def test_capm_recovers_beta_and_alpha():
    rng = np.random.default_rng(7)
    rb = rng.normal(0.0003, 0.01, 120)
    rp = 0.0005 + 2.0 * rb + rng.normal(0.0, 0.0002, 120)
    dates = pd.bdate_range("2024-01-02", periods=len(rb))
    equity = pd.DataFrame(
        {
            "date": dates,
            "equity": 1_000_000 * np.cumprod(1 + rp),
            "cash": 0.0,
            "market_value": 0.0,
            "drawdown": 0.0,
        }
    )
    benchmark = pd.Series(1000 * np.cumprod(1 + rb), index=dates, name="000300")
    stats = capm_stats(equity, benchmark)
    assert stats["beta"] == pytest.approx(2.0, abs=0.1)
    assert stats["alpha_annualized_pct"] == pytest.approx(12.6, abs=3.0)
    assert stats["r2"] > 0.95


def test_monthly_and_yearly_include_first_period():
    equity = pd.DataFrame(
        {
            "date": pd.to_datetime(["2024-01-02", "2024-01-31", "2024-02-29"]),
            "equity": [100.0, 110.0, 121.0],
        }
    )
    monthly = monthly_returns(equity)
    assert monthly[0]["month"] == 1
    assert monthly[0]["ret"] == pytest.approx(10.0)
    yearly = yearly_returns(equity)
    assert yearly[0]["year"] == 2024
    assert yearly[0]["ret"] == pytest.approx(21.0)


def test_builder_and_html_report(tmp_path):
    data = ReportDataBuilder(_result()).build()
    assert data["meta"]["benchmark"] == "000300"
    assert isinstance(data["alpha_beta"], dict)
    assert data["trades"][0]["symbol"] == "600519"
    html = render_report(data)
    assert "const REPORT" in html
    assert 'id="tab-trades"' in html
    assert "<script src=" not in html
    artifacts = HtmlReportOutput(tmp_path).emit(_result())
    run_dir = tmp_path / "report123"
    assert (run_dir / "report.html").exists()
    assert (run_dir / "report_data.json").exists()
    assert len(artifacts) == 2
