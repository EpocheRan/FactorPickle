"""Long-only execution bridge: tradeable sides, orders, fees and impact.

This is a deterministic paper-execution layer, not a broker connection.  It
turns target weights into orders at the next open, blocks impossible sides
(limit-up buys, limit-down sells, zero-volume/suspended names), applies the
actual fee schedule and a square-root impact proxy, and returns the residual
cash/blocked state.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from factor_lab.core import config


EXECUTION_SCHEMA = "factor_lab.alpha.execution_policy.v1"


@dataclass(frozen=True)
class ExecutionPolicy:
    schema: str = EXECUTION_SCHEMA
    buy_fee_bps: float = 2.44
    sell_fee_bps: float = 7.44
    impact_coeff_bps: float = 10.0
    max_participation: float = 0.10
    limit_tolerance: float = 0.005
    aum_cny: float = 1_000_000.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "buy_fee_bps": float(self.buy_fee_bps),
            "sell_fee_bps": float(self.sell_fee_bps),
            "impact_coeff_bps": float(self.impact_coeff_bps),
            "max_participation": float(self.max_participation),
            "limit_tolerance": float(self.limit_tolerance),
            "aum_cny": float(self.aum_cny),
        }


def load_execution_policy(path: str | Path | None = None) -> ExecutionPolicy:
    target = Path(path) if path is not None else Path(config.ALPHA_POLICY_DIR) / "execution.json"
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return ExecutionPolicy()
    return ExecutionPolicy(
        buy_fee_bps=float(raw.get("buy_fee_bps", 2.44)),
        sell_fee_bps=float(raw.get("sell_fee_bps", 7.44)),
        impact_coeff_bps=float(raw.get("impact_coeff_bps", 10.0)),
        max_participation=float(raw.get("max_participation", 0.10)),
        limit_tolerance=float(raw.get("limit_tolerance", 0.005)),
        aum_cny=float(raw.get("aum_cny", 1_000_000.0)),
    )


def _limit_ratio(code: str) -> float:
    code = str(code)
    if code.startswith(("30", "68")):
        return 0.20
    if code.startswith(("8", "4", "92")):
        return 0.30
    return 0.10


def tradeable_side(
    *,
    code: str,
    side: str,
    raw_open: float,
    raw_high: float,
    raw_low: float,
    raw_prev_close: float,
    volume: float,
    policy: ExecutionPolicy,
    security_master: dict[str, Any] | None = None,
) -> tuple[bool, str | None]:
    """Return (tradeable, reason) for one side at the next open."""
    if not np.isfinite(raw_open) or raw_open <= 0 or not np.isfinite(volume) or volume <= 0:
        return False, "no_open_or_zero_volume"
    master = security_master or {}
    if master.get("is_st") is True:
        return False, "st"
    list_days = master.get("list_days")
    if list_days is not None:
        try:
            if int(list_days) < 60:
                return False, "new_stock"
        except (TypeError, ValueError):
            pass
    if master.get("delisted") is True:
        return False, "delisted"
    if not np.isfinite(raw_prev_close) or raw_prev_close <= 0:
        return True, None
    ratio = _limit_ratio(code)
    limit_up = raw_prev_close * (1.0 + ratio)
    limit_down = raw_prev_close * (1.0 - ratio)
    tol = float(policy.limit_tolerance)
    if side == "buy" and np.isfinite(raw_high) and raw_high >= limit_up * (1.0 - tol) and raw_open >= limit_up * (1.0 - tol):
        return False, "limit_up_buy"
    if side == "sell" and np.isfinite(raw_low) and raw_low <= limit_down * (1.0 + tol) and raw_open <= limit_down * (1.0 + tol):
        return False, "limit_down_sell"
    return True, None


def execute_target_weights(
    previous_weights: pd.Series,
    target_weights: pd.Series,
    *,
    date: pd.Timestamp,
    context: Any,
    adv: pd.Series,
    policy: ExecutionPolicy | None = None,
    security_master: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Paper-execute target weights at one rebalance; returns executed weights/orders."""
    policy = policy or load_execution_policy()
    fields = context.fields
    raw_open = fields.get("open_raw")
    raw_high = fields.get("high_raw")
    raw_low = fields.get("low_raw")
    raw_close = fields.get("close_raw")
    volume = fields.get("volume")
    if raw_open is None or raw_close is None:
        raise ValueError("execution requires raw open/close fields")
    prev = previous_weights.reindex(target_weights.index).fillna(0.0).astype(float)
    prev = prev / prev.sum() if prev.sum() > 0 else prev
    target = target_weights.reindex(prev.index.union(target_weights.index)).fillna(0.0).astype(float)
    prev = prev.reindex(target.index).fillna(0.0)
    trades = target - prev
    executed = prev.copy()
    orders: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    buy_fee = sell_fee = 0.0
    impact_cost = 0.0
    for code, desired in trades.items():
        if abs(desired) < 1e-12:
            continue
        side = "buy" if desired > 0 else "sell"
        master = (security_master or {}).get(str(code))
        row = lambda frame: float(frame.loc[date, code]) if frame is not None and code in frame.columns and date in frame.index else float("nan")
        prev_close = float(raw_close.shift(1).loc[date, code]) if code in raw_close.columns and date in raw_close.index and len(raw_close.loc[:date]) > 1 else float("nan")
        ok, reason = tradeable_side(
            code=str(code),
            side=side,
            raw_open=row(raw_open),
            raw_high=row(raw_high),
            raw_low=row(raw_low),
            raw_prev_close=prev_close,
            volume=row(volume),
            policy=policy,
            security_master=master,
        )
        notional = abs(float(desired)) * float(policy.aum_cny)
        participation = float(notional / max(1.0, float(adv.get(code, np.nan)))) if np.isfinite(float(adv.get(code, np.nan))) else 0.0
        if participation > policy.max_participation:
            ok = False
            reason = f"participation>{policy.max_participation:g}"
        if not ok:
            blocked.append({"code": str(code), "side": side, "reason": reason, "desired_weight": float(desired)})
            orders.append({"code": str(code), "side": side, "status": "blocked", "reason": reason, "desired_weight": float(desired)})
            continue
        executed.loc[code] += float(desired)
        fee = abs(float(desired)) * (policy.buy_fee_bps if side == "buy" else policy.sell_fee_bps) / 10000.0
        if side == "buy":
            buy_fee += fee
        else:
            sell_fee += fee
        # Square-root impact: coefficient * sqrt(participation).
        impact_cost += abs(float(desired)) * float(policy.impact_coeff_bps) * np.sqrt(max(0.0, participation)) / 10000.0
        orders.append(
            {
                "code": str(code),
                "side": side,
                "status": "executed",
                "desired_weight": float(desired),
                "executed_weight": float(desired),
                "notional_cny": notional,
                "participation": participation,
            }
        )
    executed = executed.clip(lower=0.0)
    gross = float(executed.sum())
    # Keep residual cash; do not force-normalize blocked buys into other names.
    cash = max(0.0, 1.0 - gross)
    turnover = 0.5 * float((executed - prev).abs().sum() + cash + max(0.0, prev.sum() - executed.sum()))
    return {
        "schema": "factor_lab.alpha.execution_report.v1",
        "date": str(date.date()),
        "policy": policy.to_dict(),
        "previous_weights": prev,
        "target_weights": target,
        "executed_weights": executed,
        "cash_weight": cash,
        "orders": orders,
        "blocked": blocked,
        "n_orders": len(orders),
        "n_blocked": len(blocked),
        "turnover": turnover,
        "fees": buy_fee + sell_fee,
        "impact_cost": impact_cost,
        "total_cost": buy_fee + sell_fee + impact_cost,
        "gross_weight": gross,
        "security_master_available": security_master is not None,
        "data_gaps": [] if security_master is not None else ["security_master_missing: ST/new-stock/delisting flags not modeled"],
    }
