"""交易日历。

优先读 QuestDB 的 ``trading_days`` 表。**只有请求区间超过库内已覆盖的最后
日期时**才触发增量补齐：补到请求的 ``end_date`` 为止，而不是无条件补到
“今天”。因此读取历史区间（例如因子回测的 2022-2025）不会回源、不会联网。

补齐来源按 ``market.calendar_source_priority`` 顺序（缺省
``tushare -> akshare``），见 :mod:`data_interface.common.calendar_sources`。
BaoStock 不再参与交易日历补齐（登录会话不稳定、免费匿名会话经常被拒）。

两层语义（重要）：

- ``get_trading_days`` 返回**真实**交易日；区间内确实没有交易日（如春节周、
  未来日期）时返回空列表，这属于正常结果。
- ``trading_days_or_weekdays`` 在日历**不可用/未覆盖**时回退到工作日序列，
  并记录 warning；调用方（增量行情、估值同步）必须用它，避免"日历空 →
  静默一行不抓"。

补齐失败有 300 秒退避，避免每个请求都打一次外网。
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import datetime, timedelta

from .calendar_sources import CALENDAR_FETCHERS, default_calendar_priority
from .exceptions import SourceUnavailableError
from .questdb_client import QuestDBClient
from .timeutil import (
    latest_weekday,
    now_cn,
    today_str,
    weekday_range,
)

logger = logging.getLogger("DataInterface.TradingCalendar")

_CALENDAR_START = "1990-01-01"
_LAST_DATE_TTL_SECONDS = 300.0
_FILL_FAILURE_BACKOFF_SECONDS = 300.0
# 单次补齐请求的最大跨度：Tushare ``trade_cal`` 单次返回行数有限，按 4 年分块。
_FILL_CHUNK_DAYS = 4 * 366


class TradingCalendar:
    def __init__(
        self,
        db: QuestDBClient | None = None,
        *,
        config: dict | None = None,
        calendar_fetchers: Mapping[str, Callable[[str, str], list[str]]] | None = None,
        source_priority: Sequence[str] | None = None,
    ):
        self.db = db or QuestDBClient()
        self._config = config
        self._calendar_fetchers = (
            dict(calendar_fetchers) if calendar_fetchers is not None else None
        )
        self._source_priority = (
            [str(item).strip().lower() for item in source_priority]
            if source_priority is not None
            else None
        )
        self._last_date: str | None = None
        self._last_date_at: float = 0.0
        self._fill_failed_at: float = 0.0
        self._fill_error: str | None = None
        self._last_fill_source: str | None = None
        self._cache: dict[tuple[str, str], list[str]] = {}

    # ------------------------------------------------------------------ 元数据
    def _refresh_last_date(self) -> str | None:
        now = time.monotonic()
        if self._last_date is not None and now - self._last_date_at < _LAST_DATE_TTL_SECONDS:
            return self._last_date
        try:
            row = self.db.query("SELECT MAX(ts) FROM trading_days", fetch_all=False)
        except Exception as exc:  # 表不存在/连接失败：视为未覆盖
            logger.warning("交易日历 MAX(ts) 查询失败: %s", exc)
            self._last_date = None
            self._last_date_at = now
            return None
        self._last_date = row[0].strftime("%Y-%m-%d") if row and row[0] else None
        self._last_date_at = now
        return self._last_date

    def last_covered_date(self) -> str | None:
        return self._refresh_last_date()

    def has_calendar_coverage(self, end_date: str) -> bool:
        """日历是否已覆盖到 ``end_date``（用于区分「没数据」和「没日历」）。"""
        last = self._refresh_last_date()
        return bool(last and last >= end_date)

    @property
    def last_fill_error(self) -> str | None:
        return self._fill_error

    @property
    def last_fill_source(self) -> str | None:
        return self._last_fill_source

    # ------------------------------------------------------------------ 补齐来源
    def _calendar_priority(self) -> list[str]:
        if self._source_priority is not None:
            return list(self._source_priority)
        try:
            return default_calendar_priority(self._config)
        except Exception as exc:  # 配置缺失时仍能运行
            logger.warning("交易日历源配置不可用，使用默认 tushare -> akshare: %s", exc)
            return ["tushare", "akshare"]

    def _fetch_calendar_days(
        self, start_date: str, end_date: str
    ) -> tuple[list[str], str]:
        """按源优先级取区间内交易日；返回 ``(交易日列表, 成功源)``。"""
        last_error: Exception | None = None
        for source in self._calendar_priority():
            try:
                if self._calendar_fetchers is not None:
                    fetcher = self._calendar_fetchers.get(source)
                    if fetcher is None:
                        raise SourceUnavailableError(
                            f"未知的交易日历源: {source}", source=source
                        )
                else:
                    fetcher = CALENDAR_FETCHERS.get(source)
                    if fetcher is None:
                        raise SourceUnavailableError(
                            f"未知的交易日历源: {source}", source=source
                        )
                days = fetcher(start_date, end_date)
                return sorted(
                    {str(day) for day in (days or []) if start_date <= str(day) <= end_date}
                ), source
            except Exception as exc:  # noqa: BLE001 - 逐个源降级
                last_error = exc
                logger.warning(
                    "交易日历源 %s 获取失败（%s ~ %s）: %s",
                    source,
                    start_date,
                    end_date,
                    exc,
                )
        raise SourceUnavailableError(
            f"所有交易日历源均失败: {last_error}", source="calendar"
        ) from last_error

    # ------------------------------------------------------------------ 补齐
    def _date_range(self, start_date: str, end_date: str) -> list[str]:
        start = datetime.strptime(start_date, "%Y-%m-%d")
        end = datetime.strptime(end_date, "%Y-%m-%d")
        return [
            (start + timedelta(days=i)).strftime("%Y-%m-%d")
            for i in range((end - start).days + 1)
        ]

    def _needs_fill(self, end_date: str) -> bool:
        """请求区间是否超出库内已覆盖的最后日期。"""
        last_date = self._refresh_last_date()
        return last_date is None or str(end_date) > last_date

    def _fill_missing_days(self, end_date: str) -> None:
        """把 ``trading_days`` 补齐到请求的 ``end_date``；失败时退避并记录错误。"""
        if time.monotonic() - self._fill_failed_at < _FILL_FAILURE_BACKOFF_SECONDS:
            return

        last_date = self._refresh_last_date()
        start_fill = (
            _CALENDAR_START
            if not last_date
            else (datetime.strptime(last_date, "%Y-%m-%d") + timedelta(days=1)).strftime(
                "%Y-%m-%d"
            )
        )
        if start_fill > end_date:
            return

        try:
            cursor = start_fill
            while cursor <= end_date:
                chunk_end = min(
                    end_date,
                    (
                        datetime.strptime(cursor, "%Y-%m-%d")
                        + timedelta(days=_FILL_CHUNK_DAYS - 1)
                    ).strftime("%Y-%m-%d"),
                )
                trading_days, source = self._fetch_calendar_days(cursor, chunk_end)
                open_days = set(trading_days)
                rows = [
                    (datetime.strptime(date, "%Y-%m-%d"), date in open_days)
                    for date in self._date_range(cursor, chunk_end)
                ]
                self.db.execute_values(
                    "INSERT INTO trading_days (ts, is_trading) VALUES %s", rows
                )
                self._last_fill_source = source
                cursor = (
                    datetime.strptime(chunk_end, "%Y-%m-%d") + timedelta(days=1)
                ).strftime("%Y-%m-%d")
        except Exception as exc:  # noqa: BLE001 - 退避后由调用方兜底
            self._fill_failed_at = time.monotonic()
            self._fill_error = str(exc)
            logger.warning(
                "交易日历补齐失败（%.0fs 内不再重试）: %s",
                _FILL_FAILURE_BACKOFF_SECONDS,
                exc,
            )
            return

        # QuestDB 写入是 WAL 异步可见的：刚 INSERT 完立刻 SELECT MAX(ts) 可能读不到
        # 新行。这里直接相信本次写入已覆盖到 end_date，避免同一进程内重复回源。
        self._last_date = end_date
        self._last_date_at = time.monotonic()
        self._cache.clear()
        self._fill_error = None
        logger.info(
            "交易日历补齐完成 %s ~ %s（源=%s）",
            start_fill,
            end_date,
            self._last_fill_source,
        )

    # ------------------------------------------------------------------ 查询
    def get_trading_days(
        self, start_date: str, end_date: str, *, auto_fill: bool = True
    ) -> list[str]:
        """返回区间内真实交易日（可能为空：非交易日区间/未来日期）。

        仅当 ``end_date`` 超出库内已覆盖范围时才自动补齐，且只补到
        ``end_date``；已覆盖的历史区间不会触发任何外网请求。
        """
        key = (start_date, end_date)
        if key in self._cache:
            return list(self._cache[key])

        if auto_fill and self._needs_fill(end_date):
            self._fill_missing_days(end_date)

        rows = self.db.query(
            """
            SELECT DISTINCT ts
            FROM trading_days
            WHERE ts BETWEEN %s AND %s AND is_trading = true
            ORDER BY ts
            """,
            (start_date, end_date),
        )
        days = [row[0].strftime("%Y-%m-%d") for row in rows]
        self._cache[key] = days
        return list(days)

    def trading_days_or_weekdays(self, start_date: str, end_date: str) -> list[str]:
        """交易日优先；日历不可用/未覆盖时回退工作日，并记录 warning。

        该方法是**增量抓取/估值同步**唯一应该使用的入口。
        """
        try:
            days = self.get_trading_days(start_date, end_date)
        except Exception as exc:
            logger.warning("交易日历不可用，回退工作日: %s", exc)
            return weekday_range(start_date, end_date)

        if days:
            return days
        if self.has_calendar_coverage(end_date):
            return []  # 日历覆盖了该区间，确实没有交易日
        logger.warning(
            "交易日历未覆盖 %s ~ %s（last=%s），回退工作日",
            start_date,
            end_date,
            self.last_covered_date(),
        )
        return weekday_range(start_date, end_date)

    def is_trading_day(self, date: str) -> bool:
        try:
            days = self.get_trading_days(date, date)
        except Exception as exc:
            logger.warning("交易日判断失败，回退工作日: %s", exc)
            return datetime.strptime(date, "%Y-%m-%d").weekday() < 5
        if days:
            return True
        if self.has_calendar_coverage(date):
            return False
        return datetime.strptime(date, "%Y-%m-%d").weekday() < 5

    def latest_trading_day(self, lookback_days: int = 30) -> str:
        end_date = today_str()
        start_date = (now_cn() - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
        days = self.trading_days_or_weekdays(start_date, end_date)
        if days:
            return days[-1]
        return latest_weekday()


def iter_trading_days(days: Iterable[datetime]) -> list[str]:
    """辅助：datetime 序列转 ``YYYY-MM-DD``。"""
    return [item.strftime("%Y-%m-%d") for item in days]
