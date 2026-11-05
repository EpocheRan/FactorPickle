"""把 BacktestResult 转成报告用的 JSON 数据。"""

from __future__ import annotations

import math
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from typing import Any

import numpy as np
import pandas as pd

from backtest.domain.models import BacktestResult

from .alpha import (
    capm_stats,
    drawdown_episodes,
    exposure_table,
    monthly_returns,
    rolling_stats,
    yearly_returns,
)
from .trades import build_round_trips


def _num(value: Any, digits: int | None = None) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return round(number, digits) if digits is not None else number


def _date(value: Any) -> str | None:
    if value is None:
        return None
    try:
        return str(pd.Timestamp(value).date())
    except Exception:  # noqa: BLE001
        return str(value)


def _time(value: Any) -> str | None:
    if value is None:
        return None
    try:
        stamp = pd.Timestamp(value)
        return stamp.strftime("%Y-%m-%d %H:%M:%S") if stamp.time() != datetime.min.time() else _date(stamp)
    except Exception:  # noqa: BLE001
        return str(value)


def _safe(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return _num(value)
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return _num(value)
    if isinstance(value, (pd.Timestamp, datetime, date)):
        return _time(value)
    if isinstance(value, dict):
        return {str(k): _safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe(item) for item in value]
    if is_dataclass(value):
        return _safe(asdict(value))
    return str(value)


class ReportDataBuilder:
    def __init__(self, result: BacktestResult, *, risk_free_rate: float = 0.0):
        self.result = result
        self.risk_free_rate = float(risk_free_rate or result.spec.risk_free_rate)

    def build(self) -> dict[str, Any]:
        result = self.result
        equity = result.equity.copy() if result.equity is not None else pd.DataFrame()
        if not equity.empty and "date" in equity.columns:
            equity["date"] = pd.to_datetime(equity["date"], errors="coerce")
            equity = equity.dropna(subset=["date"]).sort_values("date")
            equity["ret"] = pd.to_numeric(equity["equity"], errors="coerce").pct_change() * 100.0

        benchmark_series = self._benchmark_series(result.benchmark)
        trades = result.round_trips
        if trades is None or trades.empty:
            trades = build_round_trips(result.orders)
        trades = trades.copy() if trades is not None else pd.DataFrame()
        if not trades.empty:
            for column in ("entry_date", "exit_date"):
                if column in trades.columns:
                    trades[column] = pd.to_datetime(trades[column], errors="coerce")

        alpha_beta = capm_stats(equity, result.benchmark, self.risk_free_rate)
        metrics = {key: _num(value) for key, value in (result.metrics or {}).items()}
        meta = self._meta()
        return {
            "meta": meta,
            "metrics": metrics,
            "alpha_beta": {key: _num(value) for key, value in alpha_beta.items()},
            "equity": self._equity_rows(equity),
            "benchmark": benchmark_series,
            "monthly": _safe(monthly_returns(equity, result.benchmark)),
            "yearly": _safe(yearly_returns(equity)),
            "rolling": _safe(rolling_stats(equity, result.benchmark, self.risk_free_rate)),
            "drawdowns": _safe(drawdown_episodes(equity)),
            "exposure": _safe(exposure_table(equity)),
            "trades": self._trade_rows(trades),
            "trade_summary": self._trade_summary(trades),
            "attribution": self._attribution(trades),
            "selections": self._selection_rows(result.selections),
        }

    # ------------------------------------------------------------------ parts
    def _meta(self) -> dict[str, Any]:
        result = self.result
        spec = result.spec
        meta = dict(result.meta or {})
        return {
            "run_id": result.run_id,
            "strategy": spec.strategy_name,
            "mode": spec.mode,
            "start": _date(spec.start),
            "end": _date(spec.end),
            "freq": spec.freq,
            "initial_cash": _num(spec.initial_cash),
            "engine": meta.get("engine", "—"),
            "bar_frequency": meta.get("bar_frequency", spec.freq),
            "benchmark": meta.get("benchmark") or spec.benchmark or "—",
            "benchmark_used": bool(meta.get("benchmark_used", False)),
            "risk_free_rate": _num(self.risk_free_rate),
            "fill": spec.fill,
            "max_gross_exposure": _num(spec.max_gross_exposure),
            "rejected": int(meta.get("rejected", 0) or 0),
            "orders": int(len(result.orders)) if result.orders is not None else 0,
            "trades": int(len(result.trades)) if result.trades is not None else 0,
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

    @staticmethod
    def _benchmark_series(benchmark) -> dict[str, Any]:
        if benchmark is None:
            return {}
        if isinstance(benchmark, pd.DataFrame):
            if benchmark.empty:
                return {}
            frame = benchmark.copy()
            if "date" in frame.columns:
                frame = frame.set_index("date")
            column = "close" if "close" in frame.columns else frame.columns[-1]
            series = pd.to_numeric(frame[column], errors="coerce")
        else:
            series = pd.to_numeric(benchmark.copy(), errors="coerce")
        series.index = pd.to_datetime(series.index, errors="coerce").normalize()
        series = series.dropna().sort_index()
        if series.empty:
            return {}
        return {
            "code": str(getattr(benchmark, "name", "") or ""),
            "series": [
                {"date": str(pd.Timestamp(dt).date()), "close": _num(value)}
                for dt, value in series.items()
            ],
        }

    @staticmethod
    def _equity_rows(equity: pd.DataFrame) -> list[dict]:
        if equity.empty:
            return []
        rows = []
        for row in equity.itertuples(index=False):
            rows.append(
                {
                    "date": _time(getattr(row, "date", None)),
                    "cash": _num(getattr(row, "cash", 0.0)),
                    "market_value": _num(getattr(row, "market_value", 0.0)),
                    "equity": _num(getattr(row, "equity", 0.0)),
                    "drawdown": _num(getattr(row, "drawdown", 0.0)),
                    "ret": _num(getattr(row, "ret", None)),
                }
            )
        return rows

    @staticmethod
    def _trade_rows(trades: pd.DataFrame) -> list[dict]:
        if trades is None or trades.empty:
            return []
        rows = []
        for row in trades.head(500).itertuples(index=False):
            rows.append(
                {
                    "symbol": str(getattr(row, "symbol", "")),
                    "entry_date": _date(getattr(row, "entry_date", None)),
                    "exit_date": _date(getattr(row, "exit_date", None)),
                    "entry_price": _num(getattr(row, "entry_price", None), 3),
                    "exit_price": _num(getattr(row, "exit_price", None), 3),
                    "shares": _num(getattr(row, "shares", None), 0),
                    "pnl": _num(getattr(row, "pnl", None), 2),
                    "ret": _num(getattr(row, "ret", None)),
                    "days": int(getattr(row, "days", 0) or 0),
                    "reason": str(getattr(row, "reason", "") or ""),
                }
            )
        return rows

    @staticmethod
    def _trade_summary(trades: pd.DataFrame) -> dict[str, Any]:
        if trades is None or trades.empty:
            return {}
        pnl = pd.to_numeric(trades["pnl"], errors="coerce").dropna()
        ret = pd.to_numeric(trades["ret"], errors="coerce").dropna() * 100.0
        wins = pnl[pnl > 0]
        losses = pnl[pnl < 0]
        gross_profit = float(wins.sum())
        gross_loss = float(abs(losses.sum()))
        return {
            "count": int(len(trades)),
            "win_rate_pct": _num(len(wins) / len(pnl) * 100.0) if len(pnl) else None,
            "profit_factor": _num(gross_profit / gross_loss) if gross_loss > 0 else None,
            "avg_win_pct": _num(ret[ret > 0].mean()) if (ret > 0).any() else None,
            "avg_loss_pct": _num(ret[ret < 0].mean()) if (ret < 0).any() else None,
            "payoff": _num(ret[ret > 0].mean() / abs(ret[ret < 0].mean()))
            if (ret < 0).any() and ret[ret < 0].mean() != 0
            else None,
            "expectancy_pct": _num(ret.mean()) if len(ret) else None,
            "avg_days": _num(pd.to_numeric(trades["days"], errors="coerce").mean()),
            "gross_profit": _num(gross_profit, 2),
            "gross_loss": _num(gross_loss, 2),
        }

    @staticmethod
    def _attribution(trades: pd.DataFrame) -> dict[str, Any]:
        if trades is None or trades.empty:
            return {"by_symbol": []}
        pnl = pd.to_numeric(trades["pnl"], errors="coerce").fillna(0.0)
        grouped = (
            trades.assign(pnl=pnl)
            .groupby("symbol", as_index=False)
            .agg(pnl=("pnl", "sum"), trades=("pnl", "size"))
            .sort_values("pnl", ascending=False)
        )
        rows = [
            {"symbol": str(row.symbol), "pnl": _num(row.pnl, 2), "trades": int(row.trades)}
            for row in grouped.head(15).itertuples(index=False)
        ]
        return {"by_symbol": rows}

    @staticmethod
    def _selection_rows(selections: pd.DataFrame | None) -> list[dict]:
        if selections is None or selections.empty:
            return []
        frame = selections.copy()
        if "date" in frame.columns:
            frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
            latest = frame["date"].max()
            frame = frame[frame["date"] == latest]
        rows = []
        for row in frame.head(50).itertuples(index=False):
            data = row._asdict()
            rows.append({str(k): _safe(v) for k, v in data.items()})
        return rows
