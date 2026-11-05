"""把 BacktestResult 转成报告用的 JSON 数据。"""

from __future__ import annotations

import math
import subprocess
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from backtest.domain.models import BacktestResult

from .alpha import (
    capm_stats,
    concentration_table,
    drawdown_episodes,
    exposure_table,
    monthly_returns,
    risk_stats,
    rolling_stats,
    yearly_returns,
)
from .trades import build_round_trips, remaining_lots


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


def _git_commit() -> str:
    try:
        root = Path(__file__).resolve().parents[2]
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=root,
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=2,
        ).strip()
    except Exception:  # noqa: BLE001
        return ""


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
        orders = result.orders if result.orders is not None else pd.DataFrame()
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
        monthly = monthly_returns(equity, result.benchmark)
        risk = risk_stats(equity, self.risk_free_rate, monthly)
        exposure = exposure_table(equity, orders, result.positions)
        concentration = concentration_table(result.positions, equity)
        summary = self._trade_summary(trades, orders, equity)
        bridge = self._pnl_bridge(equity, orders, trades)
        checks = self._checks(
            trades,
            summary,
            bridge,
            exposure,
            orders,
            self.result.spec.end,
            self.result.spec.max_gross_exposure,
        )
        return {
            "meta": meta,
            "metrics": metrics,
            "alpha_beta": {key: _num(value) for key, value in alpha_beta.items()},
            "risk": {key: _num(value) for key, value in risk.items()},
            "equity": self._equity_rows(equity),
            "benchmark": benchmark_series,
            "monthly": _safe(monthly),
            "yearly": _safe(yearly_returns(equity)),
            "rolling": _safe(rolling_stats(equity, result.benchmark, self.risk_free_rate)),
            "drawdowns": _safe(drawdown_episodes(equity)),
            "exposure": _safe(exposure),
            "concentration": _safe(concentration),
            "trades": self._trade_rows(trades),
            "trade_summary": summary,
            "pnl_bridge": bridge,
            "checks": checks,
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
            "universe": spec.universe,
            "universe_size": int(meta.get("universe_size", len(spec.symbols)) or 0),
            "candidate_count": int(meta.get("candidate_count", len(spec.symbols)) or 0),
            "rebalance": spec.rebalance,
            "top_n": spec.top_n,
            "weighting": spec.weighting,
            "params": _safe(dict(spec.params or {})),
            "factors": sorted(str(key) for key in (result.factor_meta or {})),
            "corporate_actions": spec.corporate_actions,
            "data_source": spec.data_source,
            "score_note": "score 为策略 rank() 原始输出，越大越优先；低换手策略 score=-turnover_ma_20d。",
            "cost_model": _safe(asdict(spec.rules)),
            "code_commit": _git_commit(),
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
        for row in trades.itertuples(index=False):
            fees = float(getattr(row, "buy_fees", 0.0) or 0.0) + float(
                getattr(row, "sell_fees", 0.0) or 0.0
            )
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
                    "fees": _num(fees, 2),
                    "reason": str(getattr(row, "reason", "") or ""),
                }
            )
        return rows

    @staticmethod
    def _trade_summary(
        trades: pd.DataFrame, orders: pd.DataFrame, equity: pd.DataFrame
    ) -> dict[str, Any]:
        if trades is None or trades.empty:
            return {}
        pnl = pd.to_numeric(trades["pnl"], errors="coerce").dropna()
        ret = pd.to_numeric(trades["ret"], errors="coerce").dropna() * 100.0
        wins = pnl[pnl > 0]
        losses = pnl[pnl < 0]
        gross_profit = float(wins.sum())
        gross_loss = float(abs(losses.sum()))
        fees_total = (
            float(pd.to_numeric(orders.get("fees", 0.0), errors="coerce").fillna(0.0).sum())
            if orders is not None and not orders.empty
            else 0.0
        )
        avg_equity = (
            float(pd.to_numeric(equity["equity"], errors="coerce").mean())
            if equity is not None and not equity.empty
            else 0.0
        )
        years = max(len(equity) / 252.0, 1.0 / 252.0) if equity is not None else 1.0
        buy_amount = sell_amount = 0.0
        if orders is not None and not orders.empty:
            frame = orders.copy()
            frame["amount"] = (
                pd.to_numeric(frame["shares"], errors="coerce").abs()
                * pd.to_numeric(frame["fill_price"], errors="coerce")
            )
            buy_amount = float(frame.loc[frame["type"].astype(str).str.lower() == "buy", "amount"].sum())
            sell_amount = float(frame.loc[frame["type"].astype(str).str.lower() == "sell", "amount"].sum())
        one_way = buy_amount / avg_equity / years * 100.0 if avg_equity > 0 else None
        two_way = (buy_amount + sell_amount) / avg_equity / years * 100.0 if avg_equity > 0 else None
        fees_bps = fees_total / avg_equity / years * 10000.0 if avg_equity > 0 else None
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
            "fees_total": _num(fees_total, 2),
            "fees_bps_annual": _num(fees_bps),
            "turnover_one_way_pct": _num(one_way),
            "turnover_two_way_pct": _num(two_way),
            "buy_amount": _num(buy_amount, 2),
            "sell_amount": _num(sell_amount, 2),
        }

    @staticmethod
    def _pnl_bridge(
        equity: pd.DataFrame, orders: pd.DataFrame, trades: pd.DataFrame
    ) -> dict[str, Any]:
        if equity is None or equity.empty or trades is None or trades.empty:
            return {}
        frame = equity.copy()
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
        frame = frame.dropna(subset=["date"]).sort_values("date")
        start_equity = float(frame.iloc[0]["equity"])
        end_equity = float(frame.iloc[-1]["equity"])
        delta = end_equity - start_equity
        lots = remaining_lots(orders)
        open_cost = sum(
            float(lot["shares"]) * float(lot["price"])
            for items in lots.values()
            for lot in items
        )
        open_fees = sum(float(lot["fees"]) for items in lots.values() for lot in items)
        open_shares = sum(float(lot["shares"]) for items in lots.values() for lot in items)
        realized = float(pd.to_numeric(trades["pnl"], errors="coerce").fillna(0.0).sum())
        final_mv = float(frame.iloc[-1].get("market_value", 0.0) or 0.0)
        unrealized = final_mv - open_cost
        residual = delta - realized - unrealized + open_fees
        denominator = abs(delta) if abs(delta) > 1e-9 else max(abs(end_equity), 1.0)
        fees_total = (
            float(pd.to_numeric(orders.get("fees", 0.0), errors="coerce").fillna(0.0).sum())
            if orders is not None and not orders.empty
            else 0.0
        )
        return {
            "start_equity": _num(start_equity, 2),
            "end_equity": _num(end_equity, 2),
            "delta": _num(delta, 2),
            "realized_pnl": _num(realized, 2),
            "unrealized_pnl": _num(unrealized, 2),
            "open_cost": _num(open_cost, 2),
            "open_fees": _num(open_fees, 2),
            "open_shares": _num(open_shares, 0),
            "open_positions": int(sum(1 for items in lots.values() if items)),
            "residual": _num(residual, 2),
            "residual_pct": _num(residual / denominator * 100.0),
            "fees_total": _num(fees_total, 2),
        }

    @staticmethod
    def _checks(
        trades: pd.DataFrame,
        summary: dict[str, Any],
        bridge: dict[str, Any],
        exposure: list[dict],
        orders: pd.DataFrame,
        end,
        max_gross_exposure: float = 0.98,
    ) -> list[dict[str, str]]:
        checks: list[dict[str, str]] = []

        def add(name: str, status: str, detail: str) -> None:
            checks.append({"name": name, "status": status, "detail": detail})

        count = int(summary.get("count", 0) or 0)
        add(
            "回合表完整性",
            "pass" if count == len(trades) else "fail",
            f"表格 {len(trades)} 条 / 汇总 {count} 条",
        )

        if trades is not None and not trades.empty and orders is not None and not orders.empty:
            sell_shares = float(
                pd.to_numeric(
                    orders.loc[orders["type"].astype(str).str.lower() == "sell", "shares"],
                    errors="coerce",
                ).fillna(0.0).abs().sum()
            )
            trip_shares = float(pd.to_numeric(trades["shares"], errors="coerce").fillna(0.0).sum())
            ok = abs(sell_shares - trip_shares) <= max(1e-4, sell_shares * 1e-9)
            add(
                "卖出股数配平",
                "pass" if ok else "fail",
                f"回合 {trip_shares:,.0f} 股 / 卖出 {sell_shares:,.0f} 股",
            )
        else:
            add("卖出股数配平", "warn", "无回合交易或无订单")

        if trades is not None and not trades.empty and "exit_date" in trades.columns:
            last_exit = pd.to_datetime(trades["exit_date"], errors="coerce").max()
            try:
                end_ts = pd.Timestamp(end)
                gap = int((end_ts - pd.Timestamp(last_exit)).days)
                status = "pass" if gap <= 45 else "warn" if gap <= 120 else "fail"
                add("交易覆盖到期末", status, f"最后出场 {pd.Timestamp(last_exit).date()}，距期末 {gap} 天")
            except Exception:  # noqa: BLE001
                add("交易覆盖到期末", "warn", "无法比较期末日期")
        else:
            add("交易覆盖到期末", "warn", "无回合交易")

        if bridge:
            residual = float(bridge.get("residual") or 0.0)
            end_equity = abs(float(bridge.get("end_equity") or 0.0)) or 1.0
            ratio = abs(residual) / end_equity
            status = "pass" if ratio <= 0.001 else "warn" if ratio <= 0.01 else "fail"
            add(
                "已实现/未实现桥",
                status,
                f"残差 {residual:,.2f}（{ratio*100:.3f}% 期末权益）",
            )
        else:
            add("已实现/未实现桥", "warn", "无足够数据")

        max_gross = max((float(item.get("gross_pct") or 0.0) for item in exposure), default=0.0)
        max_positions = max((int(item.get("positions") or 0) for item in exposure), default=0)
        if max_gross > 1.0 and max_positions == 0:
            add("持仓只数", "fail", "有仓位但持仓只数全为 0")
        else:
            add("持仓只数", "pass", f"最大 {max_positions} 只，最大仓位 {max_gross:.1f}%")

        target = float(max_gross_exposure or 0.0) * 100.0
        if target > 0:
            ratio = max_gross / target
            status = "pass" if ratio <= 1.05 else "warn" if ratio <= 1.10 else "fail"
            add(
                "仓位上限",
                status,
                f"实际最大 {max_gross:.1f}% / 目标 {target:.1f}%（含价格漂移）",
            )

        if trades is not None and not trades.empty and "reason" in trades.columns:
            reasons = trades["reason"].astype(str)
            if (reasons == "unknown").all():
                add("卖出原因", "warn", "全部为 unknown，需引擎补 reason")
            else:
                add("卖出原因", "pass", f"{reasons.nunique()} 类原因")
        else:
            add("卖出原因", "warn", "无回合交易")
        return checks

    @staticmethod
    def _attribution(trades: pd.DataFrame) -> dict[str, Any]:
        if trades is None or trades.empty:
            return {"by_symbol": [], "bottom": [], "total_pnl": 0.0, "symbols": 0}
        pnl = pd.to_numeric(trades["pnl"], errors="coerce").fillna(0.0)
        grouped = (
            trades.assign(pnl=pnl)
            .groupby("symbol", as_index=False)
            .agg(pnl=("pnl", "sum"), trades=("pnl", "size"))
            .sort_values("pnl", ascending=False)
        )
        rows = [
            {"symbol": str(row.symbol), "pnl": _num(row.pnl, 2), "trades": int(row.trades)}
            for row in grouped.itertuples(index=False)
        ]
        if len(rows) <= 30:
            top_rows = rows[:10]
            bottom_rows = rows[10:]
        else:
            top_rows = rows[:15]
            bottom_rows = rows[-15:]
        return {
            "by_symbol": top_rows,
            "bottom": bottom_rows,
            "total_pnl": _num(float(pnl.sum()), 2),
            "symbols": len(rows),
        }

    @staticmethod
    def _selection_rows(selections: pd.DataFrame | None) -> list[dict]:
        if selections is None or selections.empty:
            return []
        frame = selections.copy()
        if "date" in frame.columns:
            frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
            frame = frame.sort_values(["date", "rank"], na_position="last")
        rows = []
        for row in frame.head(2000).itertuples(index=False):
            data = row._asdict()
            rows.append({str(k): _safe(v) for k, v in data.items()})
        return rows
