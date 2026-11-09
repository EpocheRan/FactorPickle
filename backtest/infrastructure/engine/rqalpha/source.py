"""RQAlpha 日线数据源：把 FactorPickle DataBundle 适配成 RQAlpha 接口。

设计口径：
- 信号与因子由 FactorPickle 在 RQAlpha 之外计算；这里只提供**原始价**，
  用于撮合、涨跌停、估值。
- 复权、分红、拆股暂不在这里处理（v1 已知边界，见文档）。
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Iterable

import numpy as np
import pandas as pd

from rqalpha.const import TRADING_CALENDAR_TYPE
from rqalpha.interface import AbstractDataSource, ExchangeRate
from rqalpha.model.instrument import Instrument

from backtest.infrastructure.execution.a_share import limit_pct

BAR_DTYPE = np.dtype(
    [
        ("datetime", "u8"),
        ("open", "f8"),
        ("high", "f8"),
        ("low", "f8"),
        ("close", "f8"),
        ("volume", "f8"),
        ("total_turnover", "f8"),
        ("limit_up", "f8"),
        ("limit_down", "f8"),
        ("prev_close", "f8"),
        ("hfq_close", "f8"),
        ("settlement", "f8"),
        ("open_interest", "f8"),
    ]
)


class RQAlphaDataSource(AbstractDataSource):
    def __init__(
        self,
        frame: pd.DataFrame,
        instruments: Iterable[Instrument],
        calendar: Iterable[pd.Timestamp],
        rules,
        security_status=None,
        freq: str = "1d",
    ):
        self.instruments = tuple(instruments)
        self._by_id = {item.order_book_id: item for item in self.instruments}
        self._by_code = {item.order_book_id.split(".")[0]: item for item in self.instruments}
        self.rules = rules
        self.security_status = security_status
        self.freq = str(freq)
        self.is_minute = self.freq.lower() in {"1m", "5m", "15m", "30m", "60m"}
        self.dates = pd.DatetimeIndex(sorted(pd.Timestamp(item).normalize() for item in calendar))
        self.bars = self._build_bars(frame)

    def _key(self, value) -> int:
        fmt = "%Y%m%d%H%M%S" if self.is_minute else "%Y%m%d"
        return int(pd.Timestamp(value).strftime(fmt))

    # ------------------------------------------------------------------ build
    def _build_bars(self, frame: pd.DataFrame) -> dict[str, np.ndarray]:
        data = frame.copy()
        if "date" not in data.columns and "ts" in data.columns:
            data = data.rename(columns={"ts": "date"})
        data["date"] = pd.to_datetime(data["date"], errors="coerce")
        if not self.is_minute:
            data["date"] = data["date"].dt.normalize()
        data["symbol"] = data["symbol"].astype(str)
        data = data.dropna(subset=["date", "symbol"]).sort_values(["symbol", "date"])

        if "amount" not in data.columns:
            data["amount"] = data["close"].astype(float) * data["volume"].astype(float)
        data["amount"] = pd.to_numeric(data["amount"], errors="coerce").fillna(0.0)
        data["volume"] = pd.to_numeric(data["volume"], errors="coerce").fillna(0.0)

        result: dict[str, np.ndarray] = {}
        for symbol, group in data.groupby("symbol", sort=True):
            instrument = self._by_code.get(str(symbol))
            if instrument is None:
                continue
            group = group.sort_values("date")
            n = len(group)
            array = np.zeros(n, dtype=BAR_DTYPE)
            array["datetime"] = (
                group["date"]
                .dt.strftime("%Y%m%d%H%M%S" if self.is_minute else "%Y%m%d")
                .astype("uint64")
                .to_numpy()
            )
            for field in ("open", "high", "low", "close"):
                array[field] = pd.to_numeric(group[field], errors="coerce").to_numpy(dtype=float)
            array["volume"] = group["volume"].to_numpy(dtype=float)
            array["total_turnover"] = group["amount"].to_numpy(dtype=float)
            prev_close = np.roll(array["close"], 1)
            prev_close[0] = np.nan
            array["prev_close"] = prev_close
            if "hfq_close" in group.columns:
                array["hfq_close"] = pd.to_numeric(group["hfq_close"], errors="coerce").to_numpy(dtype=float)
            else:
                array["hfq_close"] = array["close"]
            array["settlement"] = array["close"]
            array["open_interest"] = 0.0

            is_st = False
            new_listing = False
            if self.security_status is not None:
                first_day = group["date"].iloc[0].date()
                is_st = bool(self.security_status.is_st(symbol, first_day))
                new_listing = bool(self.security_status.is_new_listing(symbol, first_day))
            pct = limit_pct(
                instrument.order_book_id,
                is_st=is_st,
                new_listing=new_listing,
            )
            if pct is None:
                array["limit_up"] = np.nan
                array["limit_down"] = np.nan
            else:
                array["limit_up"] = np.round(prev_close * (1.0 + pct), 2)
                array["limit_down"] = np.round(prev_close * (1.0 - pct), 2)
            result[instrument.order_book_id] = array
        return result

    # ------------------------------------------------------------------ query
    def get_instruments(self, id_or_syms=None, types=None):
        if id_or_syms is None:
            if types is None:
                return list(self.instruments)
            allowed = set(types)
            return [item for item in self.instruments if item.type in allowed]
        result: list[Instrument] = []
        seen: set[str] = set()
        for item in id_or_syms:
            key = str(item)
            instrument = self._by_id.get(key) or self._by_code.get(key)
            if instrument is not None and instrument.order_book_id not in seen:
                result.append(instrument)
                seen.add(instrument.order_book_id)
        return result

    def get_trading_calendars(self):
        return {TRADING_CALENDAR_TYPE.CN_STOCK: self.dates}

    def available_data_range(self, frequency):
        return self.dates[0].date(), self.dates[-1].date()

    def get_bar(self, instrument, dt, frequency):
        array = self.bars.get(instrument.order_book_id)
        if array is None or len(array) == 0:
            return None
        key = np.uint64(self._key(dt))
        pos = array["datetime"].searchsorted(key)
        if pos >= len(array) or array["datetime"][pos] != key:
            return None
        return array[pos]

    def history_bars(
        self,
        instrument,
        bar_count,
        frequency,
        fields,
        dt,
        skip_suspended=True,
        include_now=False,
        adjust_type="pre",
        adjust_orig=None,
    ):
        array = self.bars.get(instrument.order_book_id)
        if array is None or len(array) == 0:
            return None
        key = np.uint64(self._key(dt))
        index = array["datetime"].searchsorted(key, side="right")
        left = 0 if bar_count is None else max(0, index - int(bar_count))
        out = array[left:index]
        return out if fields is None else out[fields]

    def get_open_auction_bar(self, instrument, dt):
        row = self.get_bar(instrument, dt, "1d")
        keys = ("datetime", "open", "limit_up", "limit_down", "volume", "total_turnover")
        if row is None:
            out = {key: np.nan for key in keys}
            out["last"] = np.nan
            return out
        out = {key: row[key] for key in keys}
        out["last"] = row["open"]
        return out

    def get_open_auction_volume(self, instrument, dt):
        row = self.get_bar(instrument, dt, "1d")
        return np.nan if row is None else row["volume"]

    def get_settle_price(self, instrument, dt):
        row = self.get_bar(instrument, dt, "1d")
        return np.nan if row is None else row["close"]

    def hfq_close(self, order_book_id: str, dt) -> float:
        array = self.bars.get(order_book_id)
        if array is None or len(array) == 0:
            return float("nan")
        key = np.uint64(self._key(dt))
        pos = array["datetime"].searchsorted(key)
        if pos >= len(array) or array["datetime"][pos] != key:
            return float("nan")
        return float(array["hfq_close"][pos])

    def is_suspended(self, order_book_id, dates):
        if self.is_minute:
            return [False] * len(dates)
        array = self.bars.get(order_book_id)
        if array is None:
            return [True] * len(dates)
        keys = np.array([self._key(item) for item in dates], dtype=np.uint64)
        pos = array["datetime"].searchsorted(keys)
        result: list[bool] = []
        for key, index in zip(keys, pos):
            if index >= len(array) or array["datetime"][index] != key:
                result.append(True)
            else:
                result.append(not (array["volume"][index] > 0))
        return result

    def is_st_stock(self, order_book_id, dates):
        if self.security_status is None:
            return [False] * len(dates)
        code = order_book_id.split(".")[0]
        return [bool(self.security_status.is_st(code, item)) for item in dates]

    def get_dividend(self, instrument):
        return None

    def get_split(self, instrument):
        return None

    def get_yield_curve(self, start_date, end_date, tenor=None):
        index = pd.DatetimeIndex(pd.to_datetime([start_date, end_date]))
        return pd.DataFrame(0.0, index=index, columns=["0S"])

    def get_exchange_rate(self, trading_date, local, settlement=None):
        return ExchangeRate(1.0, 1.0, 1.0, 1.0, 1.0, 1.0)

    def get_trading_minutes_for(self, instrument, trading_dt):
        return None

    def current_snapshot(self, instrument, frequency, dt):
        raise NotImplementedError

    def get_merge_ticks(self, order_book_id_list, trading_date, last_dt=None):
        raise NotImplementedError

    def history_ticks(self, instrument, count, dt):
        raise NotImplementedError

    def get_algo_bar(self, id_or_ins, start_min, end_min, dt):
        raise NotImplementedError

    def get_futures_trading_parameters(self, instrument, dt):
        raise NotImplementedError
