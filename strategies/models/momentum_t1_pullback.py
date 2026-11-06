"""日线 T+1 强势股回调策略（v0.2 日线版，研究原型）。

只用日线 OHLCV：T 日收盘确认，T+1 开盘买入；收盘触发止损，次日开盘卖。
本文件先实现“回调型 + 简化突破”，用于验证日线口径；不含 regime/板块 PIT。
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from factors import F
from factors.library import factor
from strategies.base import EventStrategy
from strategies.pools.pool_2017_200 import POOL_2017_11_01_200
from strategies.universe import Universe


@factor("ema9", unit="price", window=9, inputs=("hfq_close",))
def _ema9(data: pd.DataFrame) -> pd.Series:
    return data["hfq_close"].ewm(span=9, adjust=False).mean()


@factor("ema21", unit="price", window=21, inputs=("hfq_close",))
def _ema21(data: pd.DataFrame) -> pd.Series:
    return data["hfq_close"].ewm(span=21, adjust=False).mean()


@factor("ema50", unit="price", window=50, inputs=("hfq_close",))
def _ema50(data: pd.DataFrame) -> pd.Series:
    return data["hfq_close"].ewm(span=50, adjust=False).mean()


@factor("ema150", unit="price", window=150, inputs=("hfq_close",))
def _ema150(data: pd.DataFrame) -> pd.Series:
    return data["hfq_close"].ewm(span=150, adjust=False).mean()


@factor("adr20", unit="pct", window=20, inputs=("high", "low", "close"))
def _adr20(data: pd.DataFrame) -> pd.Series:
    prev = data["close"].shift(1).replace(0.0, np.nan)
    rng = (data["high"] - data["low"]) / prev
    return rng.rolling(20, min_periods=10).mean() * 100.0


@factor("high20", unit="price", window=20, inputs=("hfq_high",))
def _high20(data: pd.DataFrame) -> pd.Series:
    return data["hfq_high"].rolling(20, min_periods=10).max()


@factor("volratio20", unit="ratio", window=20, inputs=("volume",))
def _volratio20(data: pd.DataFrame) -> pd.Series:
    avg = data["volume"].rolling(20, min_periods=10).mean().replace(0.0, np.nan)
    return data["volume"] / avg


class MomentumT1Pullback(EventStrategy):
    name = "momentum_t1_pullback"
    universe = Universe.explicit(POOL_2017_11_01_200)
    max_positions = 8
    params = {
        "risk_pct": 0.005,
        "max_weight": 0.12,
        "max_positions": 8,
        "adr_min": 3.0,
        "adr_max": 8.0,
        "pullback_band": 0.03,
        "breakout_buffer": 0.005,
        "volume_ratio": 1.5,
        "pullback_volume": 0.9,
        "min_mom20": 0.05,
    }

    def __init__(self, params=None):
        super().__init__(params)
        self.max_positions = int(self.params["max_positions"])
        self._entry: dict[str, float] = {}
        self._stop: dict[str, float] = {}
        self._target1: dict[str, float] = {}
        self._target2: dict[str, float] = {}
        self._partial1: set[str] = set()
        self._partial2: set[str] = set()
        self._time_half: set[str] = set()

    def factors(self):
        return [
            F.mom(20),
            F.mom(3),
            F.mom(1),
            F.atr(20),
            F.custom("ema9", window=9, unit="price"),
            F.custom("ema21", window=21, unit="price"),
            F.custom("ema50", window=50, unit="price"),
            F.custom("ema150", window=150, unit="price"),
            F.custom("adr20", window=20, unit="pct"),
            F.custom("high20", window=20, unit="price"),
            F.custom("volratio20", window=20, unit="ratio"),
        ]

    def _cleanup(self, code: str) -> None:
        for bucket in (self._entry, self._stop, self._target1, self._target2):
            bucket.pop(code, None)
        self._partial1.discard(code)
        self._partial2.discard(code)
        self._time_half.discard(code)

    def _exit(self, ctx) -> None:
        code = ctx.symbol
        hfq = float(ctx.hfq_close)
        adj = hfq / ctx.close if ctx.close > 0 else 1.0
        entry = float(self._entry.get(code) or 0.0)
        if entry <= 0:
            raw_entry = float(ctx.avg_price or ctx.close or 0.0)
            entry = raw_entry * adj if raw_entry > 0 else hfq
            self._entry[code] = entry
        d = 0.03
        stop = self._stop.setdefault(code, entry * (1.0 - d))
        target1 = self._target1.setdefault(code, entry * (1.0 + 2.0 * d))
        target2 = self._target2.setdefault(code, entry * (1.0 + 3.5 * d))
        ma21 = float(ctx.f("ema21"))
        hard = entry * (0.94 if not code.startswith(("300", "301")) else 0.92)

        if hfq < stop or hfq < hard or (math.isfinite(ma21) and hfq < ma21):
            ctx.close_position()
            return
        if hfq >= target1 and code not in self._partial1:
            ctx.sell_percent(0.34)
            self._partial1.add(code)
        if hfq >= target2 and code not in self._partial2:
            ctx.sell_percent(0.34)
            self._partial2.add(code)
        if ctx.bars_held >= 10 and hfq < entry * 1.015:
            ctx.close_position()
            return
        if ctx.bars_held >= 5 and hfq < entry * 1.005 and code not in self._time_half:
            ctx.sell_percent(0.5)
            self._time_half.add(code)

    def _candidate(self, ctx) -> float | None:
        code = ctx.symbol
        hfq = float(ctx.hfq_close)
        ema9 = float(ctx.f("ema9"))
        ema21 = float(ctx.f("ema21"))
        ema50 = float(ctx.f("ema50"))
        ema150 = float(ctx.f("ema150"))
        mom20 = float(ctx.f("mom20"))
        mom3 = float(ctx.f("mom3"))
        mom1 = float(ctx.f("mom1"))
        atr = float(ctx.f("atr20"))
        adr = float(ctx.f("adr20"))
        high20 = float(ctx.f("high20"))
        volratio = float(ctx.f("volratio20"))
        values = (hfq, ema21, ema50, ema150, mom20, mom3, atr, adr, high20, volratio)
        if not all(math.isfinite(v) for v in values) or ctx.close <= 0:
            return None
        if not (hfq > ema21 > ema50 > ema150):
            return None
        if adr < float(self.params["adr_min"]) or adr > float(self.params["adr_max"]):
            return None
        # A: 回调到 EMA21 附近，缩量，20 日仍强
        pullback = (
            hfq <= ema21 * (1.0 + float(self.params["pullback_band"]))
            and hfq >= ema21 * 0.97
            and mom3 < 0
            and mom20 > float(self.params["min_mom20"])
            and volratio < float(self.params["pullback_volume"])
        )
        # B: 收盘突破 20 日高，放量，不追涨停
        breakout = (
            hfq > high20 * (1.0 + float(self.params["breakout_buffer"]))
            and volratio > float(self.params["volume_ratio"])
            and mom1 > 0.03
            and mom1 < 0.095
        )
        if not (pullback or breakout):
            return None
        adj = hfq / ctx.close if ctx.close > 0 else 1.0
        low_hfq = float(ctx.low) * adj
        stop = min(low_hfq, ema21) - 0.3 * atr
        d = (hfq - stop) / hfq
        if not 0.02 <= d <= 0.05:
            return None
        self._stop[code] = stop
        self._entry[code] = hfq
        return float(mom20)

    def on_universe(self, contexts) -> None:
        held = [ctx for ctx in contexts.values() if ctx.pos > 1e-9]
        held_codes = {ctx.symbol for ctx in held}
        for code in list(self._entry):
            if code not in held_codes:
                self._cleanup(code)
        for ctx in held:
            self._exit(ctx)
        slots = max(0, self.max_positions - len(held))
        if slots <= 0:
            return
        candidates = []
        for code, ctx in contexts.items():
            if ctx.pos > 1e-9:
                continue
            score = self._candidate(ctx)
            if score is not None:
                candidates.append((score, ctx))
        candidates.sort(key=lambda item: item[0], reverse=True)
        for score, ctx in candidates[:slots]:
            code = ctx.symbol
            hfq = float(ctx.hfq_close)
            stop = self._stop.get(code, hfq * 0.97)
            d = max(0.02, min(0.05, (hfq - stop) / hfq))
            gap = max(0.015, min(0.035, 0.5 * float(ctx.f("adr20")) / 100.0))
            weight = min(float(self.params["risk_pct"]) / (d + gap), float(self.params["max_weight"]))
            if weight <= 0:
                continue
            self._entry[code] = hfq
            self._target1[code] = hfq * (1.0 + 2.0 * d)
            self._target2[code] = hfq * (1.0 + 3.5 * d)
            ctx.target_weight(weight)


STRATEGY = MomentumT1Pullback
