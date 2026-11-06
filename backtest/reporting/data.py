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
    normalized_capm,
    oos_stats,
    risk_stats,
    rolling_returns,
    rolling_stats,
    yearly_returns,
)
from .trades import build_round_trips, remaining_lots
from .industry import (
    industry_attribution,
    industry_exposure,
    load_index_close,
    load_industry_map,
)


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
        self.industry_map: dict[str, str] | None = None

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
        exposure = exposure_table(equity, orders, result.positions)
        alpha_beta.update(
            normalized_capm(equity, result.benchmark, exposure, self.risk_free_rate)
        )
        alternative_benchmarks: dict[str, Any] = {}
        if result.positions is not None and not result.positions.empty:
            for code in ("000852",):
                series = load_index_close(code, result.spec.start, result.spec.end)
                if series.empty:
                    continue
                stats = capm_stats(equity, series, self.risk_free_rate)
                if stats:
                    alternative_benchmarks[code] = {
                        key: _num(value) for key, value in stats.items()
                    }
        metrics = {key: _num(value) for key, value in (result.metrics or {}).items()}
        geo_excess = self._geometric_excess(equity, result.benchmark)
        if geo_excess is not None:
            metrics["excess_geometric_pct"] = geo_excess
        meta = self._meta()
        monthly = monthly_returns(equity, result.benchmark)
        monthly_excess = self._monthly_excess(monthly)
        monthly_summary = self._monthly_summary(monthly_excess)
        risk = risk_stats(equity, self.risk_free_rate, monthly)
        risk_payload = {key: _num(value) for key, value in risk.items()}
        if risk.get("annualization_note"):
            risk_payload["annualization_note"] = risk["annualization_note"]
        avg_gross = (
            float(pd.Series([float(item.get("gross_pct") or 0.0) for item in exposure]).mean())
            if exposure
            else 0.0
        )
        annualized = float(risk.get("annualized_return_pct") or 0.0)
        risk_payload["avg_gross_pct"] = _num(avg_gross)
        risk_payload["cash_drag_pct"] = (
            _num(annualized / (avg_gross / 100.0) - annualized) if avg_gross > 0 else None
        )
        concentration = concentration_table(result.positions, equity)
        if self.industry_map is None:
            self.industry_map = load_industry_map() if result.positions is not None else {}
        industry_rows = industry_exposure(result.positions, equity, self.industry_map)
        industry_pnl = industry_attribution(trades, self.industry_map)
        selection_forward = self._selection_forward(result.selections, result.positions)
        summary = self._trade_summary(trades, orders, equity)
        cost_sensitivity = self._cost_sensitivity(summary)
        bridge = self._pnl_bridge(equity, orders, trades)
        valuation = self._valuation(result.positions, equity)
        checks = self._checks(
            trades,
            summary,
            bridge,
            exposure,
            orders,
            self.result.spec.end,
            self.result.spec.max_gross_exposure,
            self.result.selections,
            int(self.result.spec.top_n or 0),
            valuation,
        )
        warnings = self._warnings(equity, monthly_summary, alpha_beta, result.spec)
        yearly = yearly_returns(equity)
        rolling = rolling_stats(equity, result.benchmark, self.risk_free_rate)
        selections_rows = self._selection_rows(result.selections)
        board_rows, bse_months = self._board_exposure(result.selections)
        regime = self._regime(equity, yearly, monthly_excess, rolling)
        if valuation and abs(float(valuation.get("gap_pct") or 0.0)) > 0.1:
            warnings.append(
                {
                    "level": "warn",
                    "text": (
                        f"持仓市值与权益市值差 {valuation.get('gap'):,.0f} "
                        f"（{valuation.get('gap_pct'):.2f}% 权益），行业暴露/净值口径不一致。"
                    ),
                }
            )
        if bse_months:
            warnings.append(
                {
                    "level": "warn",
                    "text": "选股含北交所（920 号段）：30% 涨跌幅、流动性/冲击成本未建模，历史收益不可直接外推。",
                }
            )
        if (
            regime.get("log_share_2024_pct") is not None
            and float(regime.get("ex_2024_years") or 0.0) > 1.0
            and abs(float(regime["log_share_2024_pct"])) > 40
        ):
            warnings.append(
                {
                    "level": "warn",
                    "text": f"2024 年贡献了 {regime['log_share_2024_pct']:.1f}% 的对数收益，收益高度依赖单一年份。",
                }
            )
        return {
            "meta": meta,
            "metrics": metrics,
            "alpha_beta": {key: _num(value) for key, value in alpha_beta.items()},
            "alternative_benchmarks": alternative_benchmarks,
            "risk": risk_payload,
            "warnings": warnings,
            "valuation": valuation,
            "regime": regime,
            "board_exposure": board_rows,
            "bse_months": bse_months,
            "equity": self._equity_rows(equity),
            "benchmark": benchmark_series,
            "monthly": _safe(monthly),
            "monthly_excess": _safe(monthly_excess),
            "monthly_summary": _safe(monthly_summary),
            "cost_sensitivity": cost_sensitivity,
            "oos": _safe(oos_stats(equity)),
            "rolling_returns": _safe(rolling_returns(equity)),
            "yearly": _safe(yearly),
            "rolling": _safe(rolling),
            "drawdowns": _safe(drawdown_episodes(equity)),
            "exposure": _safe(exposure),
            "concentration": _safe(concentration),
            "industry_exposure": _safe(industry_rows),
            "industry_attribution": _safe(industry_pnl),
            "trades": self._trade_rows(trades),
            "trade_summary": summary,
            "pnl_bridge": bridge,
            "checks": checks,
            "attribution": self._attribution(trades),
            "selections": selections_rows,
            "selection_forward": selection_forward,
        }

    # ------------------------------------------------------------------ parts
    @staticmethod
    def _monthly_excess(monthly: list[dict]) -> list[dict]:
        rows = []
        for item in monthly or []:
            ret = item.get("ret")
            bench = item.get("bench_ret")
            if ret is None or bench is None:
                continue
            rows.append(
                {
                    "year": int(item.get("year")),
                    "month": int(item.get("month")),
                    "ret": _num(ret),
                    "bench_ret": _num(bench),
                    "excess": _num(float(ret) - float(bench)),
                }
            )
        return rows

    @staticmethod
    def _monthly_summary(rows: list[dict]) -> dict[str, Any]:
        values = [float(item["excess"]) for item in rows if item.get("excess") is not None]
        if not values:
            return {}
        series = pd.Series(values, dtype="float64")
        std = float(series.std(ddof=1)) if len(series) > 1 else 0.0
        t_stat = float(series.mean() / (std / math.sqrt(len(series)))) if std > 0 else float("nan")
        p_value = float(math.erfc(abs(t_stat) / math.sqrt(2.0))) if math.isfinite(t_stat) else None
        return {
            "months": len(values),
            "win_rate_pct": _num(float((series > 0).mean() * 100.0)),
            "mean_pct": _num(float(series.mean())),
            "std_pct": _num(std),
            "ir": _num(float(series.mean() / std * math.sqrt(12.0))) if std > 0 else None,
            "t_stat": _num(t_stat),
            "p_value": _num(p_value, 3),
        }

    @staticmethod
    def _warnings(
        equity: pd.DataFrame,
        monthly_summary: dict[str, Any],
        alpha: dict[str, Any],
        spec: Any,
    ) -> list[dict[str, str]]:
        warnings: list[dict[str, str]] = []
        observations = len(equity) if equity is not None else 0
        if observations < 756:
            months = (
                pd.to_datetime(equity["date"], errors="coerce").dt.to_period("M").nunique()
                if equity is not None and not equity.empty
                else 0
            )
            t_value = alpha.get("alpha_t")
            warnings.append(
                {
                    "level": "warn",
                    "text": (
                        f"样本不足 3 年：{observations} 个交易日 / {months} 个月，"
                        f"Alpha t={_num(t_value, 2)}，月度 IR={_num(monthly_summary.get('ir'), 2)}；"
                        "统计显著性有限，结论不宜外推。"
                    ),
                }
            )
        if str(getattr(spec, "corporate_actions", "off")) == "off":
            warnings.append(
                {
                    "level": "warn",
                    "text": (
                        "公司行为未启用：分红、送转、除权除息未计入净值；"
                        "对高股息组合可能产生 3–5pp 量级偏差。"
                    ),
                }
            )
        return warnings

    @staticmethod
    def _geometric_excess(equity: pd.DataFrame, benchmark) -> float | None:
        if equity is None or equity.empty or "equity" not in equity.columns:
            return None
        frame = equity.copy()
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
        frame = frame.dropna(subset=["date"]).sort_values("date")
        if len(frame) < 2:
            return None
        portfolio = float(frame.iloc[-1]["equity"]) / float(frame.iloc[0]["equity"]) - 1.0
        if benchmark is None:
            return None
        if isinstance(benchmark, pd.DataFrame):
            if benchmark.empty:
                return None
            bench = benchmark.copy()
            if "date" in bench.columns:
                bench = bench.set_index("date")
            column = "close" if "close" in bench.columns else bench.columns[-1]
            series = pd.to_numeric(bench[column], errors="coerce").dropna()
        else:
            series = pd.to_numeric(benchmark.copy(), errors="coerce").dropna()
        if len(series) < 2:
            return None
        bench_total = float(series.iloc[-1]) / float(series.iloc[0]) - 1.0
        return _num(((1.0 + portfolio) / (1.0 + bench_total) - 1.0) * 100.0)

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
            "eligible_count": int(meta.get("eligible_count", 0) or 0),
            "rebalance": spec.rebalance,
            "top_n": spec.top_n,
            "weighting": spec.weighting,
            "params": _safe(dict(spec.params or {})),
            "factors": sorted(str(key) for key in (result.factor_meta or {})),
            "corporate_actions": spec.corporate_actions,
            "data_source": spec.data_source,
            "score_note": (
                "score 为策略 rank() 原始输出，越大越优先；低换手策略 score=-turnover_ma_20d。"
                if "low_turnover" in str(spec.strategy_name)
                else "score 为策略打分，越大越优先；具体因子与阈值见策略文件。"
            ),
            "cost_model": _safe(asdict(spec.rules)),
            "cost_model_text": (
                f"佣金 {spec.rules.commission_rate*100:.3f}%（最低 {spec.rules.min_commission:.0f}）/ "
                f"印花税 {spec.rules.stamp_tax_schedule[-1][1]*100:.3f}% / "
                f"过户费 {spec.rules.transfer_fee_rate*100:.4f}% / "
                f"{'T+1' if spec.rules.t_plus_1 else 'T+0'} / 整手 {spec.rules.lot_size}"
            ),
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
        one_way = (
            (buy_amount + sell_amount) / 2.0 / avg_equity / years * 100.0
            if avg_equity > 0
            else None
        )
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
        selections: pd.DataFrame | None = None,
        top_n: int = 0,
        valuation: dict[str, Any] | None = None,
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

        if selections is not None and not selections.empty and "date" in selections.columns:
            frame = selections.copy()
            frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
            if "weight" in frame.columns:
                frame = frame[pd.to_numeric(frame["weight"], errors="coerce").fillna(0.0) > 0]
            counts = frame.groupby("date")["symbol"].nunique()
            if top_n > 0 and len(counts):
                median_count = float(counts.median())
                status = "pass" if median_count >= top_n else "warn" if median_count >= top_n - 1 else "fail"
                add(
                    "调仓持仓数",
                    status,
                    f"目标 {top_n} 只 / 实际中位 {median_count:.0f} 只",
                )
                gross_map = {
                    str(item.get("date")): float(item.get("gross_pct") or 0.0)
                    for item in exposure
                }
                gross_values = [
                    gross_map[str(pd.Timestamp(dt).date())]
                    for dt in counts.index
                    if str(pd.Timestamp(dt).date()) in gross_map
                ]
                target_pct = float(max_gross_exposure or 0.0) * 100.0
                if gross_values and target_pct > 0:
                    median_gross = float(pd.Series(gross_values).median())
                    status = (
                        "pass"
                        if median_gross >= target_pct * 0.95
                        else "warn"
                        if median_gross >= target_pct * 0.85
                        else "fail"
                    )
                    add(
                        "调仓仓位",
                        status,
                        f"目标 {target_pct:.0f}% / 调仓日中位 {median_gross:.1f}%",
                    )
                if exposure:
                    exp = pd.DataFrame(exposure)
                    exp["date"] = pd.to_datetime(exp["date"], errors="coerce")
                    exp = exp.dropna(subset=["date"]).sort_values("date")
                    after_counts = []
                    for dt in counts.index:
                        end_dt = pd.Timestamp(dt) + pd.Timedelta(days=10)
                        window = exp[(exp["date"] >= pd.Timestamp(dt)) & (exp["date"] <= end_dt)]
                        if not window.empty:
                            after_counts.append(int(window.iloc[0]["positions"]))
                    if after_counts:
                        median_hold = float(pd.Series(after_counts).median())
                        status = (
                            "pass"
                            if median_hold >= top_n
                            else "warn"
                            if median_hold >= top_n - 1
                            else "fail"
                        )
                        add(
                            "调仓后持仓数",
                            status,
                            f"目标 {top_n} 只 / 调仓后中位 {median_hold:.0f} 只",
                        )
                if exposure:
                    exp = pd.DataFrame(exposure)
                    exp["date"] = pd.to_datetime(exp["date"], errors="coerce")
                    exp = exp.dropna(subset=["date"]).set_index("date")
                    month_end = exp["gross_pct"].resample("ME").last().dropna()
                    if len(month_end) and target_pct > 0:
                        median_me = float(month_end.median())
                        status = (
                            "pass"
                            if median_me >= target_pct * 0.90
                            else "warn"
                            if median_me >= target_pct * 0.80
                            else "fail"
                        )
                        add(
                            "月末仓位",
                            status,
                            f"目标 {target_pct:.0f}% / 月末中位 {median_me:.1f}%",
                        )
                    if len(exp):
                        under = float((pd.to_numeric(exp["gross_pct"], errors="coerce") < 96.0).mean() * 100.0)
                        under_status = "pass" if under <= 5.0 else "warn" if under <= 20.0 else "fail"
                        add("仓位低于96%占比", under_status, f"{under:.1f}% 的交易日")
                        low_pos = float((pd.to_numeric(exp["positions"], errors="coerce") < top_n).mean() * 100.0)
                        low_status = "pass" if low_pos <= 5.0 else "warn"
                        add("持仓低于目标占比", low_status, f"{low_pos:.1f}% 的交易日 / 目标 {top_n} 只")
                    me_under = float((month_end < 90.0).mean() * 100.0) if len(month_end) else 0.0
                    me_status = "pass" if me_under <= 5.0 else "warn" if me_under <= 20.0 else "fail"
                    add("月末仓位低于90%占比", me_status, f"{me_under:.1f}% 的月份")
                if valuation:
                    gap_pct = abs(float(valuation.get("gap_pct") or 0.0))
                    add(
                        "持仓市值对账",
                        "pass" if gap_pct <= 0.1 else "fail",
                        f"差异 {valuation.get('gap'):,.0f}（{gap_pct:.2f}% 权益）",
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
    def _cost_sensitivity(summary: dict[str, Any]) -> list[dict]:
        turnover = summary.get("turnover_one_way_pct")
        if turnover is None:
            return []
        return [
            {
                "bps_per_side": int(bps),
                "annual_drag_pct": _num(float(turnover) / 100.0 * bps / 100.0),
            }
            for bps in (0, 5, 10, 20, 30)
        ]

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
        top_total = float(sum(float(row["pnl"] or 0.0) for row in top_rows))
        total = float(pnl.sum())
        return {
            "by_symbol": top_rows,
            "bottom": bottom_rows,
            "total_pnl": _num(total, 2),
            "top_total": _num(top_total, 2),
            "top_share_pct": _num(top_total / total * 100.0) if abs(total) > 1e-9 else None,
            "symbols": len(rows),
        }

    @staticmethod
    def _selection_forward(
        selections: pd.DataFrame | None,
        positions: pd.DataFrame | None,
    ) -> dict[str, Any]:
        if (
            selections is None
            or selections.empty
            or positions is None
            or positions.empty
            or "close" not in positions.columns
        ):
            return {}
        sel = selections.copy()
        sel["date"] = pd.to_datetime(sel["date"], errors="coerce")
        if "weight" in sel.columns:
            sel = sel[pd.to_numeric(sel["weight"], errors="coerce").fillna(0.0) > 0]
        sel_dates = sorted(pd.Timestamp(d) for d in sel["date"].dropna().unique())
        if len(sel_dates) < 2:
            return {}
        pos = positions.copy()
        pos["date"] = pd.to_datetime(pos["date"], errors="coerce").dt.normalize()
        pos["symbol"] = pos["symbol"].astype(str)
        pivot = pos.pivot_table(index="date", columns="symbol", values="close", aggfunc="last")
        returns = []
        for idx, dt in enumerate(sel_dates[:-1]):
            next_dt = sel_dates[idx + 1]
            if dt not in pivot.index or next_dt not in pivot.index:
                continue
            symbols = sel.loc[sel["date"] == dt, "symbol"].astype(str).tolist()
            for symbol in symbols:
                if symbol not in pivot.columns:
                    continue
                start = pivot.at[dt, symbol]
                end = pivot.at[next_dt, symbol]
                if pd.notna(start) and pd.notna(end) and float(start) > 0:
                    returns.append(float(end) / float(start) - 1.0)
        if not returns:
            return {}
        series = pd.Series(returns, dtype="float64")
        return {
            "periods": len(sel_dates) - 1,
            "observations": len(series),
            "selected_total": int(len(sel)),
            "coverage_pct": _num(len(series) / len(sel) * 100.0) if len(sel) else None,
            "mean_ret_pct": _num(float(series.mean() * 100.0)),
            "win_rate_pct": _num(float((series > 0).mean() * 100.0)),
            "median_ret_pct": _num(float(series.median() * 100.0)),
        }

    @staticmethod
    def _valuation(positions: pd.DataFrame | None, equity: pd.DataFrame) -> dict[str, Any]:
        if positions is None or positions.empty or "date" not in positions.columns:
            return {}
        pos = positions.copy()
        pos["date"] = pd.to_datetime(pos["date"], errors="coerce").dt.normalize()
        last_date = pos["date"].max()
        latest = pos[pos["date"] == last_date]
        value_col = "market_value" if "market_value" in latest.columns else "close"
        positions_mv = float(pd.to_numeric(latest[value_col], errors="coerce").fillna(0.0).sum())
        eq = equity.copy()
        eq["date"] = pd.to_datetime(eq["date"], errors="coerce").dt.normalize()
        row = eq[eq["date"] == last_date]
        equity_mv = float(row["market_value"].sum()) if not row.empty else 0.0
        equity_total = float(row["equity"].sum()) if not row.empty else 0.0
        gap = positions_mv - equity_mv
        return {
            "date": str(last_date.date()) if pd.notna(last_date) else None,
            "positions_mv": _num(positions_mv, 2),
            "equity_mv": _num(equity_mv, 2),
            "gap": _num(gap, 2),
            "gap_pct": _num(gap / equity_total * 100.0) if equity_total else None,
        }

    @staticmethod
    def _board(symbol: str) -> str:
        text = str(symbol)
        if text.startswith("920") or text.startswith("8"):
            return "北交所"
        if text.startswith(("300", "301")):
            return "创业板"
        if text.startswith("688"):
            return "科创板"
        return "主板"

    def _regime(
        self,
        equity: pd.DataFrame,
        yearly: list[dict],
        monthly_excess: list[dict],
        rolling: list[dict],
    ) -> dict[str, Any]:
        if equity is None or equity.empty:
            return {}
        frame = equity.copy()
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
        frame = frame.dropna(subset=["date"]).sort_values("date")
        values = pd.to_numeric(frame["equity"], errors="coerce")
        total = float(values.iloc[-1] / values.iloc[0] - 1.0) if len(values) > 1 else 0.0
        year_2024 = next((item["ret"] for item in yearly if int(item.get("year") == 2024)), None)
        log_share = None
        if year_2024 is not None and total > -1 and year_2024 > -100:
            denominator = math.log1p(total)
            if abs(denominator) > 1e-12:
                log_share = math.log1p(year_2024 / 100.0) / denominator * 100.0
        ex_2024 = None
        if year_2024 is not None:
            ex_2024 = ((1.0 + total) / (1.0 + year_2024 / 100.0) - 1.0) * 100.0
        excess_total = sum(float(item.get("excess") or 0.0) for item in monthly_excess)
        nov_2023 = next(
            (
                float(item.get("excess") or 0.0)
                for item in monthly_excess
                if int(item.get("year")) == 2023 and int(item.get("month")) == 11
            ),
            None,
        )
        beta_values = [float(item["beta"]) for item in rolling if int(item.get("window")) == 60 and item.get("beta") is not None]
        alpha_values = [float(item["alpha_pct"]) for item in rolling if int(item.get("window")) == 60 and item.get("alpha_pct") is not None]
        returns = values.pct_change().dropna()
        return {
            "return_2024_pct": _num(year_2024),
            "log_share_2024_pct": _num(log_share),
            "ex_2024_return_pct": _num(ex_2024),
            "ex_2024_years": _num(max((len(values) / 252.0) - 1.0, 0.0)),
            "excess_total_pct": _num(excess_total),
            "nov_2023_excess_pct": _num(nov_2023),
            "nov_2023_excess_share_pct": _num(nov_2023 / excess_total * 100.0)
            if nov_2023 is not None and abs(excess_total) > 1e-9
            else None,
            "rolling_beta_min": _num(min(beta_values)) if beta_values else None,
            "rolling_beta_max": _num(max(beta_values)) if beta_values else None,
            "rolling_alpha_min_pct": _num(min(alpha_values)) if alpha_values else None,
            "rolling_alpha_max_pct": _num(max(alpha_values)) if alpha_values else None,
            "days_abs_gt_7pct": int((returns.abs() > 0.07).sum()),
        }

    @staticmethod
    def _board_exposure(selections: pd.DataFrame | None) -> tuple[list[dict], list[dict]]:
        if selections is None or selections.empty or "date" not in selections.columns:
            return [], []
        frame = selections.copy()
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
        frame = frame.dropna(subset=["date"])
        frame["board"] = frame["symbol"].astype(str).map(ReportDataBuilder._board)
        latest = frame["date"].max()
        latest_rows = frame[frame["date"] == latest]
        total = len(latest_rows)
        exposure = [
            {"board": str(name), "count": int(count), "weight_pct": _num(count / total * 100.0)}
            for name, count in latest_rows["board"].value_counts().items()
        ]
        bse = frame[frame["board"] == "北交所"].groupby(frame["date"].dt.to_period("M")).size()
        months = [
            {"month": str(month), "count": int(count)}
            for month, count in bse.items()
            if count > 0
        ]
        return exposure, months

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
            data["board"] = ReportDataBuilder._board(str(getattr(row, "symbol", "")))
            rows.append({str(k): _safe(v) for k, v in data.items()})
        return rows
