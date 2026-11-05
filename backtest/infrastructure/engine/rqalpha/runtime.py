"""RQAlpha 回测运行期上下文（单线程，一次 run 一份）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

import pandas as pd


@dataclass
class PendingOrder:
    code: str
    shares: int
    reason: str = ""


@dataclass
class StopState:
    stop: float | None = None
    trailing_pct: float | None = None
    high_water: float = float("-inf")
    hold_bars: int | None = None


@dataclass
class Runtime:
    strategy: Any
    panel: Any
    source: Any
    cost: Any
    rules: Any
    cross_plan: Any | None
    initial_cash: float
    symbols: tuple[str, ...]
    selections: pd.DataFrame
    start: date
    end: date
    max_gross_exposure: float = 0.98
    last_date: date | None = None
    pending: list[PendingOrder] = field(default_factory=list)
    stops: dict[str, StopState] = field(default_factory=dict)
    bars_held: dict[str, int] = field(default_factory=dict)
    entry_dates: dict[str, date] = field(default_factory=dict)
    rejected: list[dict[str, Any]] = field(default_factory=list)


_RUNTIME: Runtime | None = None


def set_runtime(runtime: Runtime) -> None:
    global _RUNTIME
    _RUNTIME = runtime


def get_runtime() -> Runtime:
    if _RUNTIME is None:
        raise RuntimeError("RQAlpha runtime 未初始化")
    return _RUNTIME


def clear_runtime() -> None:
    global _RUNTIME
    _RUNTIME = None
