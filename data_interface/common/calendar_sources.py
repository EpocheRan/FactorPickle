"""交易日历数据源。

`trading_days` 表的补齐来源。当前使用：

- ``tushare``：Tushare Pro ``trade_cal``（SSE），复用进程内共享的
  ``TushareSource``（同一套限流/重试/配额），2000 积分档即可用；
- ``akshare``：AkShare ``tool_trade_date_hist_sina``，作为无 token 兜底。

BaoStock 已不再参与交易日历补齐：其登录会话不稳定，且免费匿名会话经常
返回「用户名或密码错误」，会让每个新进程都卡在日历补齐上。

每个 fetcher 接收 ``(start_date, end_date)``（``YYYY-MM-DD``）并返回区间内
的真实交易日列表（``YYYY-MM-DD``，升序、去重）。区间内确实没有交易日时返回
空列表属于正常结果；调用失败才抛 ``SourceUnavailableError``。
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from .config import get_config
from .exceptions import SourceUnavailableError

logger = logging.getLogger("DataInterface.CalendarSource")

CalendarFetcher = Callable[[str, str], list[str]]

DEFAULT_CALENDAR_SOURCE_PRIORITY = ("tushare", "akshare")


def _compact(value: str) -> str:
    return str(value).replace("-", "")


def _to_iso(value: Any) -> str:
    """把 ``20240102`` / ``2024-01-02`` / ``date`` 统一成 ``YYYY-MM-DD``。"""

    if hasattr(value, "strftime"):
        return value.strftime("%Y-%m-%d")
    text = str(value).strip()
    if len(text) >= 10 and text[4] in "-/":
        return text[:10].replace("/", "-")
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    raise ValueError(f"无法解析的交易日: {value!r}")


def _in_range(value: str, start_date: str, end_date: str) -> bool:
    return start_date <= value <= end_date


def fetch_tushare_calendar(
    start_date: str,
    end_date: str,
    *,
    config: dict[str, Any] | None = None,
) -> list[str]:
    """Tushare Pro ``trade_cal``：返回区间内 SSE 开市日。"""

    try:
        from data_interface.financial.sources.tushare_source import (
            get_shared_tushare_source,
        )
    except ImportError as exc:  # pragma: no cover - 依赖缺失时给出明确错误
        raise SourceUnavailableError(
            f"Tushare 日历源不可用: {type(exc).__name__}: {exc}", source="tushare"
        ) from exc

    source = get_shared_tushare_source(config)
    rows = source.fetch_trade_cal(
        _compact(start_date), _compact(end_date), exchange="SSE"
    )
    if not rows:
        raise SourceUnavailableError(
            f"Tushare trade_cal 在 {start_date} ~ {end_date} 未返回任何日历行",
            source="tushare",
        )

    days: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        is_open = str(row.get("is_open")).strip().lower()
        if is_open not in {"1", "1.0", "true"}:
            continue
        calendar_date = row.get("cal_date")
        if calendar_date in (None, ""):
            continue
        days.add(_to_iso(calendar_date))
    return sorted(day for day in days if _in_range(day, start_date, end_date))


def fetch_akshare_calendar(
    start_date: str,
    end_date: str,
    *,
    config: dict[str, Any] | None = None,
) -> list[str]:
    """AkShare 交易日历史：返回区间内开市日（无 token 兜底）。"""

    try:
        import akshare as ak
    except ImportError as exc:  # pragma: no cover - 依赖缺失时给出明确错误
        raise SourceUnavailableError(
            f"AkShare 日历源不可用: {type(exc).__name__}: {exc}", source="akshare"
        ) from exc

    try:
        frame = ak.tool_trade_date_hist_sina()
    except Exception as exc:
        raise SourceUnavailableError(
            f"AkShare 交易日历获取失败: {type(exc).__name__}: {exc}", source="akshare"
        ) from exc
    if frame is None or len(frame) == 0:
        raise SourceUnavailableError(
            "AkShare 交易日历返回空", source="akshare"
        )

    column = "trade_date" if "trade_date" in frame.columns else frame.columns[0]
    days: set[str] = set()
    for value in frame[column].tolist():
        try:
            calendar_date = _to_iso(value)
        except ValueError:
            continue
        if _in_range(calendar_date, start_date, end_date):
            days.add(calendar_date)
    return sorted(days)


CALENDAR_FETCHERS: dict[str, CalendarFetcher] = {
    "tushare": fetch_tushare_calendar,
    "akshare": fetch_akshare_calendar,
}


def default_calendar_priority(config: dict[str, Any] | None = None) -> list[str]:
    """读取 ``market.calendar_source_priority``，缺省为 tushare → akshare。"""

    cfg = config if config is not None else get_config()
    raw = (cfg.get("market") or {}).get("calendar_source_priority")
    if not raw:
        return list(DEFAULT_CALENDAR_SOURCE_PRIORITY)
    return [str(item).strip().lower() for item in raw if str(item).strip()]
