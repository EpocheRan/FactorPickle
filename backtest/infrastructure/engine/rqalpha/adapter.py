"""FinBridge 策略 → RQAlpha 回调适配。

时序：
- T 日 ``handle_bar``：看 T 日收盘，算信号/止损，只**排队**订单；
- T+1 ``open_auction``：把排队订单按开盘价提交，T+1 成交；
- 这样避免 RQAlpha 日线 ``current_bar`` 的“当日收盘成交”前视。
"""

from __future__ import annotations

from typing import Any

import pandas as pd
from rqalpha.api import order_shares, subscribe
from rqalpha.const import POSITION_DIRECTION
from rqalpha.environment import Environment

from strategies.context import StrategyContext

from .instruments import code_of, to_order_book_id
from .runtime import PendingOrder, StopState, get_runtime


def _bar(bar_dict, oid: str):
    """RQAlpha 的 BarMap 只有 __contains__/__getitem__，没有 dict.get。"""
    if oid in bar_dict:
        return bar_dict[oid]
    return None


class StrategyAdapter:
    def __init__(self):
        self._bar_index = 0

    # ------------------------------------------------------------------ hooks
    def init(self, context) -> None:
        runtime = get_runtime()
        subscribe([to_order_book_id(code) for code in runtime.symbols])

    def open_auction(self, context, bar_dict) -> None:
        self._submit_pending(bar_dict)

    def handle_bar(self, context, bar_dict) -> None:
        runtime = get_runtime()
        self._bar_index += 1
        self._manage_stops(context, bar_dict)
        if runtime.cross_plan is None:
            self._on_single(context, bar_dict)
        else:
            self._on_cross(context, bar_dict)
        if runtime.source.is_minute:
            # 分钟模式：下一根 1m 开盘成交（matching_type=next_bar）。
            self._submit_pending(bar_dict)
        self._update_bars_held(context)

    def after_trading(self, context) -> None:
        return None

    def _submit_pending(self, bar_dict) -> None:
        runtime = get_runtime()
        for order in list(runtime.pending):
            oid = to_order_book_id(order.code)
            bar = _bar(bar_dict, oid)
            if bar is not None:
                limit_up = float(getattr(bar, "limit_up", float("nan")))
                limit_down = float(getattr(bar, "limit_down", float("nan")))
                open_price = float(bar.open)
                if order.shares > 0 and limit_up == limit_up and open_price >= limit_up - 1e-9:
                    runtime.rejected.append(
                        {"symbol": order.code, "reason": "limit_up", "shares": order.shares}
                    )
                    continue
                if order.shares < 0 and limit_down == limit_down and open_price <= limit_down + 1e-9:
                    runtime.rejected.append(
                        {"symbol": order.code, "reason": "limit_down", "shares": order.shares}
                    )
                    continue
            try:
                order_shares(oid, int(order.shares))
            except Exception as exc:  # noqa: BLE001 - 记录拒单原因，不中断回测
                runtime.rejected.append(
                    {"symbol": order.code, "reason": type(exc).__name__, "shares": order.shares}
                )
        runtime.pending.clear()

    # ------------------------------------------------------------------ single
    def _on_single(self, context, bar_dict) -> None:
        runtime = get_runtime()
        if not runtime.symbols:
            return
        code = runtime.symbols[0]
        oid = to_order_book_id(code)
        bar = _bar(bar_dict, oid)
        if bar is None:
            return
        position = self._position(context, oid)
        quantity = float(getattr(position, "quantity", 0.0) or 0.0)
        equity = float(context.portfolio.total_value)
        weight = float(getattr(position, "market_value", 0.0) or 0.0) / equity if equity > 0 else 0.0
        now = pd.Timestamp(Environment.get_instance().trading_dt)
        ctx = StrategyContext(
            symbol=code,
            dt=now if runtime.source.is_minute else now.normalize(),
            open=float(bar.open),
            high=float(bar.high),
            low=float(bar.low),
            close=float(bar.close),
            hfq_close=runtime.source.hfq_close(oid, Environment.get_instance().trading_dt),
            pos=quantity,
            cash=float(context.portfolio.cash),
            equity=equity,
            weight=weight,
            bars_held=int(runtime.bars_held.get(code, 0)),
            panel=runtime.panel,
        )
        runtime.strategy.on_bar(ctx)
        self._apply_intent(ctx, oid, code, float(bar.close), position)

    # ------------------------------------------------------------------ cross
    def _on_cross(self, context, bar_dict) -> None:
        runtime = get_runtime()
        plan = runtime.cross_plan
        if plan is None:
            return
        dt = pd.Timestamp(Environment.get_instance().trading_dt).normalize()
        target = plan.target_for(dt)
        positions = self._positions(context)

        if target is not None:
            for oid, position in positions.items():
                code = code_of(oid)
                if code not in target:
                    self._queue_sell(code, self._sellable(position, code), "rebalance_out")
            equity = float(context.portfolio.total_value)
            total_weight = sum(float(item) for item in target.values())
            scale = (
                min(1.0, float(runtime.max_gross_exposure) / total_weight)
                if total_weight > 0
                else 1.0
            )
            for code, weight in target.items():
                oid = to_order_book_id(code)
                bar = _bar(bar_dict, oid)
                if bar is None or float(bar.close) <= 0:
                    continue
                weight = float(weight) * scale
                position = positions.get(oid)
                current = float(getattr(position, "quantity", 0.0) or 0.0)
                sellable = self._sellable(position, code)
                target_shares = int(equity * float(weight) / float(bar.close) // 100 * 100)
                delta = target_shares - int(current)
                if abs(delta) * float(bar.close) < max(0.002 * equity, float(bar.close) * 100):
                    continue
                if delta > 0:
                    self._queue_buy(code, delta, "rebalance_in")
                elif delta < 0:
                    self._queue_sell(code, min(-delta, sellable), "rebalance_trim")
            return

        top_n = int(getattr(plan, "top_n", 0))
        buffer = int(getattr(plan, "buffer", 0))
        for oid, position in positions.items():
            code = code_of(oid)
            rank = plan.rank_at(dt, code)
            if rank is not None and float(rank) > top_n + buffer:
                self._queue_sell(code, self._sellable(position, code), "buffer_out")

    # ------------------------------------------------------------------ stops
    def _manage_stops(self, context, bar_dict) -> None:
        runtime = get_runtime()
        for code, state in list(runtime.stops.items()):
            oid = to_order_book_id(code)
            position = self._position(context, oid)
            quantity = float(getattr(position, "quantity", 0.0) or 0.0)
            if quantity <= 0:
                runtime.stops.pop(code, None)
                continue
            bar = _bar(bar_dict, oid)
            if bar is None:
                continue
            if state.trailing_pct is not None:
                state.high_water = max(state.high_water, float(bar.high))
                state.stop = state.high_water * (1.0 - float(state.trailing_pct))
            if state.hold_bars is not None and runtime.bars_held.get(code, 0) >= state.hold_bars:
                self._queue_sell(code, self._sellable(position, code), "time_stop")
                continue
            if state.stop is not None:
                trigger = float(bar.low) if runtime.source.is_minute else float(bar.close)
                if trigger <= float(state.stop):
                    self._queue_sell(code, self._sellable(position, code), "stop_loss")

    # ------------------------------------------------------------------ intent
    def _apply_intent(self, ctx: StrategyContext, oid: str, code: str, price: float, position) -> None:
        runtime = get_runtime()
        intent = ctx.intent
        sellable = self._sellable(position, code)
        equity = float(ctx.equity)
        if intent.target_weight is not None:
            weight = min(float(intent.target_weight), float(runtime.max_gross_exposure))
            target_shares = int(equity * weight / price // 100 * 100) if price > 0 else 0
            delta = target_shares - int(ctx.pos)
            if delta > 0:
                self._queue_buy(code, delta, "target_weight")
            elif delta < 0:
                self._queue_sell(code, min(-delta, sellable), "target_weight")
        if intent.buy_shares is not None:
            self._queue_buy(code, int(intent.buy_shares), "buy_shares")
        if intent.sell_shares is not None:
            self._queue_sell(code, min(int(intent.sell_shares), sellable), "sell_shares")
        if intent.sell_percent is not None:
            shares = int(ctx.pos * float(intent.sell_percent) // 100 * 100)
            self._queue_sell(code, min(shares, sellable), "sell_percent")
        if intent.close_position:
            self._queue_sell(code, sellable, "close_position")

        stop = intent.stop_loss
        if stop is None and intent.stop_loss_pct is not None:
            base = float(getattr(position, "avg_price", 0.0) or 0.0) or price
            stop = base * (1.0 - float(intent.stop_loss_pct))
        if stop is not None or intent.trailing_pct is not None or intent.hold_bars is not None:
            runtime.stops[code] = StopState(
                stop=None if stop is None else float(stop),
                trailing_pct=None if intent.trailing_pct is None else float(intent.trailing_pct),
                high_water=float(price),
                hold_bars=None if intent.hold_bars is None else int(intent.hold_bars),
            )
        intent.clear()

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _today():
        return pd.Timestamp(Environment.get_instance().trading_dt).date()

    def _sellable(self, position, code: str) -> float:
        """日线用 RQAlpha 的 sellable；分钟模式由适配器执行 T+1。"""
        runtime = get_runtime()
        quantity = float(getattr(position, "quantity", 0.0) or 0.0)
        if not runtime.source.is_minute:
            return float(getattr(position, "sellable", 0.0) or 0.0)
        entry = runtime.entry_dates.get(code)
        return quantity if entry is not None and entry < self._today() else 0.0

    def _queue_buy(self, code: str, shares: int, reason: str) -> None:
        if shares > 0:
            runtime = get_runtime()
            runtime.entry_dates[code] = self._today()
            self._queue(code, int(shares), reason)

    def _queue_sell(self, code: str, shares: float, reason: str) -> None:
        if shares > 0:
            self._queue(code, -int(shares), reason)

    def _queue(self, code: str, shares: int, reason: str) -> None:
        runtime = get_runtime()
        if any(order.code == code for order in runtime.pending):
            return
        runtime.pending.append(PendingOrder(code=code, shares=shares, reason=reason))

    @staticmethod
    def _position(context, oid: str):
        return context.portfolio.get_position(oid, POSITION_DIRECTION.LONG)

    def _positions(self, context) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for oid, position in context.portfolio.positions.items():
            if float(getattr(position, "quantity", 0.0) or 0.0) > 0:
                result[str(oid)] = position
        return result

    def _update_bars_held(self, context) -> None:
        runtime = get_runtime()
        current = pd.Timestamp(Environment.get_instance().trading_dt).date()
        new_day = runtime.last_date != current
        runtime.last_date = current
        held = {
            code_of(str(oid))
            for oid, position in context.portfolio.positions.items()
            if float(getattr(position, "quantity", 0.0) or 0.0) > 0
        }
        if new_day:
            for code in held:
                runtime.bars_held[code] = runtime.bars_held.get(code, 0) + 1
        for code in list(runtime.bars_held):
            if code not in held:
                runtime.bars_held.pop(code, None)
                runtime.entry_dates.pop(code, None)
