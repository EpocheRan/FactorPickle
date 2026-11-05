"""RQAlpha 引擎：EngineRequest → rqalpha.run_func → BacktestResult。"""

from __future__ import annotations

import os
import tempfile
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

# RQAlpha 会触发 matplotlib 字体扫描；把缓存放到可写目录，避免每次 run 重建。
_MPL_DIR = Path(tempfile.gettempdir()) / "finbridge-mpl"
_MPL_DIR.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("MPLCONFIGDIR", str(_MPL_DIR))
os.environ.setdefault("MPLBACKEND", "Agg")

import rqalpha  # noqa: E402
from rqalpha import run_func  # noqa: E402

from backtest.application.ports import EngineRequest
from backtest.domain.errors import EngineError
from backtest.domain.models import BacktestResult, ExecutionReport
from backtest.infrastructure.execution.ledger import (
    PortfolioLedger,
    equity_metrics,
    reconcile,
    reconcile_positions,
)

from .adapter import StrategyAdapter
from .cost import FinBridgeCostDecider
from .instruments import build_instruments, code_of
from .runtime import Runtime, clear_runtime, set_runtime
from .source import RQAlphaDataSource


def _safe_float(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("nan")


class RQAlphaEngine:
    name = "rqalpha"

    def __init__(self):
        self.last_raw: dict | None = None
        self.last_ledger = None
        self.last_ledger_obj = None

    def run(self, request: EngineRequest) -> BacktestResult:
        frame = request.data.frame
        all_symbols = tuple(sorted({str(item) for item in frame["symbol"].unique()}))
        if request.cross_plan is not None:
            # 横截面只把候选集交给 RQAlpha；全 A 5000+ 只全订阅会把事件循环放大 50–200 倍。
            trade_symbols = tuple(
                sorted({str(item) for item in request.cross_plan.candidates})
            )
            universe_size = int(request.cross_plan.universe_size)
        else:
            trade_symbols = tuple(
                sorted(
                    {
                        str(item)
                        for item in (
                            getattr(request.strategy, "symbols", ())
                            or request.spec.symbols
                            or all_symbols
                        )
                    }
                )
            )
            universe_size = len(trade_symbols)
        if not trade_symbols:
            trade_symbols = all_symbols
            universe_size = len(all_symbols)
        trade_set = set(trade_symbols)
        trade_frame = frame[frame["symbol"].astype(str).isin(trade_set)].copy()
        if trade_frame.empty:
            trade_frame = frame.copy()
            trade_symbols = all_symbols
            universe_size = len(all_symbols)

        instruments = build_instruments(
            trade_symbols,
            request.data.security_status,
            lot_size=int(request.rules.lot_size),
        )
        source = RQAlphaDataSource(
            trade_frame,
            instruments,
            request.calendar.dates,
            request.rules,
            request.data.security_status,
            freq=self._frequency(request.spec.freq),
        )
        cost = FinBridgeCostDecider(request.rules)
        selections = (
            request.cross_plan.selections.copy()
            if request.cross_plan is not None
            else pd.DataFrame(columns=["date", "symbol", "score", "rank", "weight"])
        )
        runtime = Runtime(
            strategy=request.strategy,
            panel=request.panel,
            source=source,
            cost=cost,
            rules=request.rules,
            cross_plan=request.cross_plan,
            initial_cash=float(request.spec.initial_cash),
            symbols=trade_symbols,
            universe_size=universe_size,
            selections=selections,
            start=request.spec.start,
            end=request.spec.end,
            max_gross_exposure=float(request.spec.max_gross_exposure),
        )
        adapter = StrategyAdapter()
        set_runtime(runtime)
        try:
            raw = run_func(
                config=self._config(request, runtime),
                init=adapter.init,
                open_auction=adapter.open_auction,
                handle_bar=adapter.handle_bar,
                after_trading=adapter.after_trading,
            )
            self.last_raw = raw
        except Exception as exc:  # noqa: BLE001 - 统一成 EngineError
            raise EngineError(f"RQAlpha 回测失败: {type(exc).__name__}: {exc}") from exc
        finally:
            clear_runtime()
        return self._to_result(request, raw, runtime)

    # ------------------------------------------------------------------ config
    @staticmethod
    def _frequency(value) -> str:
        text = str(value or "d").strip().lower()
        if text in ("d", "1d", "day", "daily"):
            return "1d"
        if text in ("1m", "5m", "15m", "30m", "60m", "min", "minute"):
            # RQAlpha 开源事件源只原生生成 1m；5m/15m 先按 1m 跑，再聚合。
            return "1m"
        return "1d"

    @classmethod
    def _config(cls, request: EngineRequest, runtime: Runtime) -> dict:
        frequency = cls._frequency(request.spec.freq)
        minute = frequency != "1d"
        return {
            "base": {
                "start_date": str(request.spec.start),
                "end_date": str(request.spec.end),
                "frequency": frequency,
                "run_type": "b",
                "accounts": {"stock": float(request.spec.initial_cash)},
                "benchmark": None,
                "data_bundle_path": ".",
                "capital_gain_tax_rate": 0,
                "persist": False,
            },
            "mod": {
                "sys_progress": {"enabled": False},
                "sys_accounts": {
                    "enabled": True,
                    # 分钟模式由适配器执行 T+1，规避 RQAlpha 1m 结算的 sellable 问题。
                    "stock_t1": bool(request.rules.t_plus_1) and not minute,
                },
                "sys_risk": {"enabled": True},
                "sys_simulation": {
                    "enabled": True,
                    "price_limit": bool(request.rules.limit_up_down),
                    "volume_limit": False,
                    "inactive_limit": bool(request.rules.suspension),
                    "slippage": 0.0,
                    "matching_type": "next_bar" if minute else None,
                },
                "sys_transaction_cost": {"enabled": False},
                "sys_analyser": {
                    "enabled": True,
                    "plot": False,
                    "output_file": None,
                    "report_save_path": None,
                    "record": True,
                    "benchmark": None,
                    "strategy_name": request.spec.strategy_name,
                },
                "finbridge": {
                    "enabled": True,
                    "lib": "backtest.infrastructure.engine.rqalpha.mod",
                    "priority": 200,
                },
            },
        }

    # ------------------------------------------------------------------ result
    def _to_result(self, request: EngineRequest, raw: dict, runtime: Runtime) -> BacktestResult:
        analyser = raw.get("sys_analyser") or {}
        portfolio = analyser.get("portfolio")
        trades = analyser.get("trades")

        minute = bool(getattr(runtime.source, "is_minute", False))
        orders, trades_out = self._orders_from_trades(
            trades, minute=minute, reasons=runtime.order_reasons
        )
        engine_equity = self._equity_from_portfolio(
            portfolio, request.spec.initial_cash, minute=minute
        )
        engine_metrics = equity_metrics(
            engine_equity,
            float(request.spec.initial_cash),
            benchmark=None if minute else request.benchmark,
            risk_free_rate=float(request.spec.risk_free_rate),
            periods_per_year=252.0,
        )

        if minute:
            # 分钟模式不走日线账本：RQAlpha 组合是唯一净值来源。
            equity = engine_equity
            metrics = engine_metrics
            recon: dict[str, float] = {}
            common_recon: dict[str, float] = {}
            self.last_ledger = None
            self.last_ledger_obj = None
        else:
            ledger_frame = request.data.frame
            ledger_frame = ledger_frame[
                (ledger_frame["date"] >= pd.Timestamp(request.spec.start))
                & (ledger_frame["date"] <= pd.Timestamp(request.spec.end))
                & (ledger_frame["symbol"].astype(str).isin(set(runtime.symbols)))
            ]
            ledger_data = ledger_frame[["date", "symbol", "close"]].copy()
            ledger_runner = PortfolioLedger(
                initial_cash=float(request.spec.initial_cash),
                data=ledger_data,
                orders=orders,
                symbols=runtime.symbols,
                benchmark=request.benchmark,
                risk_free_rate=float(request.spec.risk_free_rate),
                corporate_actions=request.corporate_actions,
            )
            ledger = ledger_runner.run()
            self.last_ledger = ledger
            self.last_ledger_obj = ledger_runner
            equity = engine_equity.copy() if not engine_equity.empty else ledger.equity.copy()
            metrics = engine_metrics if engine_metrics else ledger.metrics
            recon = reconcile(
                ledger.equity,
                engine_equity[["date", "equity"]] if not engine_equity.empty else None,
            )
            common_recon = reconcile_positions(
                ledger.positions, analyser.get("stock_positions")
            )

        rejected = (
            pd.DataFrame(runtime.rejected)
            if runtime.rejected
            else pd.DataFrame(columns=["date", "symbol", "reason", "requested", "filled"])
        )
        report = ExecutionReport(rejected=rejected)

        meta = {
            "engine": "rqalpha",
            "rqalpha_version": rqalpha.__version__,
            "freq": str(request.spec.freq),
            "bar_frequency": "1m" if minute else "1d",
            "fill": request.spec.fill,
            "metrics_source": "engine",
            "benchmark": request.spec.benchmark,
            "benchmark_used": bool(request.benchmark is not None and "beta" in metrics),
            "benchmark_points": int(metrics.get("benchmark_points", 0)),
            "ledger_reconciliation": recon,
            "ledger_reconciliation_common": common_recon,
            "engine_metrics": engine_metrics,
            "orders_filled": int(len(orders)),
            "trades": int(len(trades_out)),
            "rejected": int(len(rejected)),
            "universe_size": int(runtime.universe_size),
            "candidate_count": int(len(runtime.symbols)),
            "stops": "strategy_managed",
            "t_plus_1": bool(request.rules.t_plus_1),
            "price_limit": bool(request.rules.limit_up_down),
            "corporate_action_approximation": bool(
                request.corporate_actions is not None
                and not request.corporate_actions.empty
            ),
        }
        if request.cross_plan is not None:
            meta.update(
                {
                    "mode": "cross",
                    "rebalance_days": int(len(request.cross_plan.rebalance_dates)),
                    "top_n": int(request.cross_plan.top_n),
                    "buffer": int(request.cross_plan.buffer),
                }
            )

        return BacktestResult(
            run_id="",
            spec=request.spec,
            metrics=metrics,
            equity=equity,
            orders=orders,
            trades=trades_out,
            selections=runtime.selections,
            factor_meta=dict(getattr(request.panel, "meta", {}) or {}),
            execution_report=report,
            corporate_actions=request.corporate_actions,
            meta=meta,
            benchmark=request.benchmark,
            positions=None if minute else (ledger.positions if self.last_ledger else None),
        )

    # ------------------------------------------------------------------ convert
    @staticmethod
    def _orders_from_trades(
        trades: pd.DataFrame | None,
        *,
        minute: bool = False,
        reasons: dict[str, str] | None = None,
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        if trades is None or len(trades) == 0:
            return (
                pd.DataFrame(columns=["type", "symbol", "date", "shares", "fill_price", "fees", "created", "status", "reason"]),
                pd.DataFrame(),
            )
        frame = trades.reset_index(drop=True) if "datetime" in trades.columns else trades.reset_index()
        side = frame["side"].astype(str).str.upper()
        order_type = np.where(side.str.contains("BUY"), "buy", "sell")
        dt = pd.to_datetime(frame["datetime"], errors="coerce")
        if not minute:
            dt = dt.dt.normalize()
        reason_map = reasons or {}
        order_ids = frame["order_id"].astype(str) if "order_id" in frame.columns else pd.Series("", index=frame.index)
        orders = pd.DataFrame(
            {
                "type": order_type,
                "symbol": frame["order_book_id"].astype(str).map(code_of),
                "date": dt,
                "shares": frame["last_quantity"].astype(float).abs(),
                "fill_price": frame["last_price"].astype(float),
                "fees": frame["transaction_cost"].astype(float),
                "created": dt,
                "status": "FILLED",
                "reason": order_ids.map(reason_map).fillna("unknown"),
            }
        )
        return orders, frame

    @staticmethod
    def _equity_from_portfolio(
        portfolio: pd.DataFrame | None,
        initial_cash: float,
        *,
        minute: bool = False,
    ) -> pd.DataFrame:
        if portfolio is None or len(portfolio) == 0:
            return pd.DataFrame(columns=["date", "cash", "market_value", "equity", "drawdown"])
        frame = portfolio.reset_index() if "date" not in portfolio.columns else portfolio.copy()
        if "datetime" in frame.columns and "date" not in frame.columns:
            frame = frame.rename(columns={"datetime": "date"})
        date_column = "date" if "date" in frame.columns else frame.columns[0]
        frame = frame.rename(columns={date_column: "date", "total_value": "equity"})
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
        if not minute:
            frame["date"] = frame["date"].dt.normalize()
        frame["equity"] = pd.to_numeric(frame["equity"], errors="coerce").fillna(initial_cash)
        frame["cash"] = pd.to_numeric(frame["cash"], errors="coerce").fillna(initial_cash)
        frame["market_value"] = pd.to_numeric(frame["market_value"], errors="coerce").fillna(0.0)
        frame["drawdown"] = frame["equity"] / frame["equity"].cummax() - 1.0
        return frame[["date", "cash", "market_value", "equity", "drawdown"]].reset_index(drop=True)
