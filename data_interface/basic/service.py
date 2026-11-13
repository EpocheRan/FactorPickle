"""P0 基础资料同步服务。

按数据集自身的天然粒度取数，避免用「逐代码」放大请求量：

======  ==================  ==================================================
数据集  粒度                说明
======  ==================  ==================================================
stock_basic_history  快照   每次运行写一份 L/P/D 全市场名单快照
namechange           区间   按公告日分年拉取，分页去重
new_share            区间   按上市日分年拉取，分页去重
suspend_d            交易日 每个交易日全市场一次（S 停牌 / R 复牌）
stk_limit            交易日 每个交易日全市场一次
disclosure_date      报告期 每个报告期全市场一次（分页）
forecast             自然日 每个自然日全市场一次（公告可能落在周末）
express              报告期 每个报告期全市场一次（分页；单次上限 2000 行）
======  ==================  ==================================================

服务层只负责取数与归一；游标、pending 重试与任务状态由 ``acquisition`` 落库。
限流/权限/额度异常向上抛出（``STOP_ERRORS``），由调用方决定暂停或标记 BLOCKED。
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta
from typing import Any, Callable, Sequence

from data_interface.common.config import get_config
from data_interface.common.exceptions import (
    DailyQuotaExceededError,
    PermissionDeniedError,
    RateLimitExceededError,
    SourceUnavailableError,
)
from data_interface.common.timeutil import now_cn, today_str
from data_interface.common.trading_calendar import TradingCalendar

from .models import DATASET_MODE, P0_DATASETS
from .normalizer import MAPPERS, map_stock_basic
from .repository import BasicRepository

logger = logging.getLogger("DataInterface.Basic.Service")

STOP_ERRORS = (
    RateLimitExceededError,
    DailyQuotaExceededError,
    PermissionDeniedError,
    SourceUnavailableError,
)

#: Tushare 单次返回行数上限（new_share / express 实测 2000）
PAGE_SIZE = 2000

#: 空日期只在最近 N 天内重试，避免历史空日（周末/无停牌）永久挂起
EMPTY_RETRY_DAYS = 3

#: 每个交易日都应有数据的表：历史空日也要记 pending 重试（仍受尝试次数上限约束）
RETRY_ALL_EMPTY = frozenset({"stk_limit"})


def compact(day: str | date | datetime) -> str:
    return str(day)[:10].replace("-", "")


def quarter_ends(start_date: str, end_date: str) -> list[str]:
    """区间起点到终点之间需要回补的报告期，返回 ``YYYYMMDD`` 列表。

    下界取「起始年上一年年报」（``start.year - 1`` 的 12-31）：这些报表虽然
    报告期在窗口之前，但披露日落在窗口内，是 PIT 对齐的第一批数据。
    """
    start = date.fromisoformat(str(start_date)[:10])
    end = date.fromisoformat(str(end_date)[:10])
    lower = date(start.year - 1, 12, 31)
    periods: list[str] = []
    for year in range(start.year - 1, end.year + 1):
        for month, day in ((3, 31), (6, 30), (9, 30), (12, 31)):
            period = date(year, month, day)
            if lower <= period <= end:
                periods.append(period.strftime("%Y%m%d"))
    return sorted(set(periods))


def latest_quarter_end(as_of: str | date | None = None) -> str:
    day = date.fromisoformat(str(as_of or today_str())[:10])
    for month, day_of_month in ((12, 31), (9, 30), (6, 30), (3, 31)):
        period = date(day.year, month, day_of_month)
        if period <= day:
            return period.strftime("%Y%m%d")
    return date(day.year - 1, 12, 31).strftime("%Y%m%d")


def _should_retry(day: str, dataset: str = "") -> bool:
    if dataset in RETRY_ALL_EMPTY:
        return True
    try:
        age = (date.fromisoformat(today_str()) - date.fromisoformat(day)).days
    except ValueError:
        return False
    return 0 <= age <= EMPTY_RETRY_DAYS


def _day_ts(day: str) -> datetime:
    return datetime.combine(date.fromisoformat(day), time.min)


class BasicDataService:
    def __init__(
        self,
        *,
        config: dict[str, Any] | None = None,
        repository: BasicRepository | None = None,
        tushare: Any | None = None,
        trading_calendar: TradingCalendar | None = None,
    ):
        self.config = config or get_config()
        self.settings = self.config.get("basic", {}) or {}
        self.repository = repository or BasicRepository(config=self.config)
        if tushare is None:
            from data_interface.financial.sources.tushare_source import (
                get_shared_tushare_source,
            )

            tushare = get_shared_tushare_source(self.config)
        self.tushare = tushare
        self._calendar = trading_calendar

    # ------------------------------------------------------------------ 日历
    @property
    def calendar(self) -> TradingCalendar:
        if self._calendar is None:
            self._calendar = TradingCalendar()
        return self._calendar

    def latest_ts(self, dataset: str):
        return self.repository.latest_ts(dataset)

    def _days(self, dataset: str, start: str, end: str) -> list[str]:
        if DATASET_MODE[dataset] == "days_trading":
            days = self.calendar.trading_days_or_weekdays(start, end)
            return [str(day)[:10] for day in days]
        # forecast 的公告日可能落在周末/节假日，必须按自然日扫描
        first = date.fromisoformat(start)
        last = date.fromisoformat(end)
        return [
            (first + timedelta(days=offset)).isoformat()
            for offset in range((last - first).days + 1)
        ]

    # ------------------------------------------------------------------ 分页
    def _paginate(self, fetch: Callable[..., list[dict[str, Any]]], **kwargs) -> list[dict[str, Any]]:
        collected: list[dict[str, Any]] = []
        offset = 0
        while True:
            page = fetch(offset=offset, limit=PAGE_SIZE, **kwargs) or []
            collected.extend(page)
            if len(page) < PAGE_SIZE:
                break
            offset += PAGE_SIZE
        return collected

    # ------------------------------------------------------------------ 主数据
    def sync_stock_basic_history(self) -> dict[str, Any]:
        """写一份 L/P/D 全市场名单快照（不覆盖 ``stock_basic_info``）。"""
        snapshot = datetime.combine(now_cn().date(), time.min)
        ingested = now_cn()
        raw: list[dict[str, Any]] = []
        status_counts: dict[str, int] = {}
        errors: list[str] = []
        for status in ("L", "P", "D"):
            try:
                batch = self.tushare.fetch_stock_basic(list_status=status) or []
            except STOP_ERRORS:
                raise
            except Exception as exc:  # noqa: BLE001
                # L 是主名单，失败直接抛出；P/D 失败不阻断已拿到的数据
                if status == "L":
                    raise
                logger.warning("stock_basic(%s) 拉取失败: %s", status, exc)
                errors.append(f"{status}: {exc}")
                continue
            status_counts[status] = len(batch)
            raw.extend(batch)

        rows = map_stock_basic(raw, snapshot, ingested_at=ingested)
        written = self.repository.upsert("stock_basic_history", rows)
        logger.info(
            "stock_basic_history 快照 %s: %s 行（L=%s P=%s D=%s）",
            snapshot.date(),
            f"{written:,}",
            status_counts.get("L", 0),
            status_counts.get("P", 0),
            status_counts.get("D", 0),
        )
        return {
            "dataset": "stock_basic_history",
            "snapshot_date": snapshot.date().isoformat(),
            "done": True,
            "rows_written": written,
            "status_counts": status_counts,
            "errors": errors,
        }

    # ------------------------------------------------------------------ 区间
    def sync_range(
        self,
        dataset: str,
        start_date: str,
        end_date: str,
        *,
        cursor: str | None = None,
        max_years: int = 2,
    ) -> dict[str, Any]:
        """按年分块拉取区间型数据（namechange / new_share）。"""
        if dataset not in ("namechange", "new_share"):
            raise ValueError(f"{dataset!r} 不是区间型数据集")
        start = date.fromisoformat(str(start_date)[:10])
        end = date.fromisoformat(str(end_date)[:10])
        chunks: list[tuple[str, str, str]] = []
        for year in range(start.year, end.year + 1):
            lo = max(date(year, 1, 1), start)
            hi = min(date(year, 12, 31), end)
            chunks.append((date(year, 1, 1).isoformat(), lo.isoformat(), hi.isoformat()))
        cursor_day = str(cursor)[:10] if cursor else None
        todo = [chunk for chunk in chunks if cursor_day is None or chunk[0] >= cursor_day]
        batch = todo[: max(1, int(max_years))]
        fetch = (
            self.tushare.fetch_namechange
            if dataset == "namechange"
            else self.tushare.fetch_new_share
        )
        mapper = MAPPERS[dataset]
        ingested = now_cn()
        rows_written = 0
        processed: list[str] = []
        errors: list[str] = []
        for _key, lo, hi in batch:
            try:
                raw = self._paginate(fetch, start_date=lo, end_date=hi)
            except STOP_ERRORS:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning("%s 拉取 %s~%s 失败: %s", dataset, lo, hi, exc)
                errors.append(f"{lo}~{hi}: {exc}")
                processed.append(lo)
                continue
            rows = mapper(raw, ingested_at=ingested)
            rows_written += self.repository.upsert(dataset, rows)
            processed.append(lo)
            logger.info("%s %s~%s: %s 行", dataset, lo, hi, f"{len(rows):,}")
        remaining = todo[len(batch) :]
        return {
            "dataset": dataset,
            "done": not remaining,
            "next_cursor": remaining[0][0] if remaining else None,
            "rows_written": rows_written,
            "processed_chunks": processed,
            "total_chunks": len(todo),
            "errors": errors[:10],
        }

    # ------------------------------------------------------------------ 按日
    def sync_days(
        self,
        dataset: str,
        start_date: str,
        end_date: str,
        *,
        cursor: str | None = None,
        pending: Sequence[str] = (),
        max_days: int = 20,
    ) -> dict[str, Any]:
        """按交易日/自然日批量拉取（suspend_d / stk_limit / forecast）。"""
        if DATASET_MODE.get(dataset) not in ("days_trading", "days_calendar"):
            raise ValueError(f"{dataset!r} 不是按日数据集")
        start = str(start_date)[:10]
        end = str(end_date)[:10]
        all_days = self._days(dataset, start, end)
        pending_days = sorted(
            {str(day)[:10] for day in (pending or []) if start <= str(day)[:10] <= end}
        )
        eligible = [day for day in all_days if not cursor or day >= cursor]
        days = sorted(set(pending_days) | set(eligible))
        batch = days[: max(1, int(max_days))]

        if dataset == "suspend_d":
            fetch: Callable[..., list[dict[str, Any]]] = self.tushare.fetch_suspend_d
        elif dataset == "stk_limit":
            fetch = self.tushare.fetch_stk_limit
        elif dataset == "forecast":
            fetch = self.tushare.fetch_forecast
        else:  # pragma: no cover - 上面已校验
            raise ValueError(f"未知按日数据集: {dataset!r}")
        mapper = MAPPERS[dataset]

        rows_written = 0
        processed: list[str] = []
        empty_days: list[str] = []
        retry_pending: list[str] = []
        errors: list[dict[str, str]] = []
        for index, day in enumerate(batch, 1):
            ingested = now_cn()
            try:
                if dataset == "forecast":
                    raw = self._paginate(fetch, ann_date=compact(day))
                else:
                    raw = fetch(trade_date=compact(day)) or []
            except STOP_ERRORS:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning("%s 拉取 %s 失败: %s", dataset, day, exc)
                errors.append({"date": day, "error": str(exc)[:200]})
                if _should_retry(day, dataset):
                    retry_pending.append(day)
                processed.append(day)
                continue
            rows = mapper(raw, ingested_at=ingested)
            if not rows:
                empty_days.append(day)
                if _should_retry(day, dataset):
                    retry_pending.append(day)
                processed.append(day)
                continue
            rows_written += self.repository.upsert(dataset, rows)
            processed.append(day)
            if index % 20 == 0 or index == len(batch):
                logger.info(
                    "  [%s/%s] %s 累计 %s 行（空日 %s）",
                    index,
                    len(batch),
                    day,
                    f"{rows_written:,}",
                    len(empty_days),
                )

        remaining = days[len(batch) :]
        return {
            "dataset": dataset,
            "done": not remaining,
            "next_cursor": remaining[0] if remaining else None,
            "rows_written": rows_written,
            "processed_days": processed,
            "pending": retry_pending,
            "empty_days": empty_days,
            "errors": errors,
            "total_days": len(days),
        }

    # ------------------------------------------------------------------ 按报告期
    def sync_periods(
        self,
        dataset: str,
        start_date: str,
        end_date: str,
        *,
        cursor: str | None = None,
        max_periods: int = 4,
    ) -> dict[str, Any]:
        """按报告期批量拉取（disclosure_date / express）。"""
        if DATASET_MODE.get(dataset) != "periods":
            raise ValueError(f"{dataset!r} 不是报告期数据集")
        periods = quarter_ends(start_date, end_date)
        todo = [period for period in periods if not cursor or period >= str(cursor)]
        batch = todo[: max(1, int(max_periods))]
        if dataset == "disclosure_date":
            fetch: Callable[..., list[dict[str, Any]]] = self.tushare.fetch_disclosure_date
            period_key = "end_date"
        else:
            fetch = self.tushare.fetch_express
            period_key = "period"
        mapper = MAPPERS[dataset]
        rows_written = 0
        processed: list[str] = []
        empty_periods: list[str] = []
        errors: list[dict[str, str]] = []
        for period in batch:
            ingested = now_cn()
            try:
                raw = self._paginate(fetch, **{period_key: period})
            except STOP_ERRORS:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning("%s 拉取报告期 %s 失败: %s", dataset, period, exc)
                errors.append({"period": period, "error": str(exc)[:200]})
                processed.append(period)
                continue
            rows = mapper(raw, ingested_at=ingested)
            if not rows:
                empty_periods.append(period)
            else:
                rows_written += self.repository.upsert(dataset, rows)
            processed.append(period)

        remaining = todo[len(batch) :]
        return {
            "dataset": dataset,
            "done": not remaining,
            "next_cursor": remaining[0] if remaining else None,
            "rows_written": rows_written,
            "processed_periods": processed,
            "empty_periods": empty_periods,
            "errors": errors,
            "total_periods": len(todo),
        }

    # ------------------------------------------------------------------ 统一入口
    def sync(
        self,
        dataset: str,
        start_date: str,
        end_date: str,
        *,
        cursor: str | None = None,
        pending: Sequence[str] = (),
        max_units: int | None = None,
    ) -> dict[str, Any]:
        """按数据集粒度分发；``max_units`` 是本次最多处理的日/报告期/年块数。"""
        if dataset not in P0_DATASETS:
            raise ValueError(f"未知 P0 数据集: {dataset!r}")
        mode = DATASET_MODE[dataset]
        if mode == "snapshot":
            return self.sync_stock_basic_history()
        if mode == "days_trading" or mode == "days_calendar":
            return self.sync_days(
                dataset,
                start_date,
                end_date,
                cursor=cursor,
                pending=pending,
                max_days=max_units
                or int(self.settings.get("batch_days", 20)),
            )
        if mode == "periods":
            return self.sync_periods(
                dataset,
                start_date,
                end_date,
                cursor=cursor,
                max_periods=max_units
                or int(self.settings.get("batch_periods", 4)),
            )
        return self.sync_range(
            dataset,
            start_date,
            end_date,
            cursor=cursor,
            max_years=max_units or int(self.settings.get("batch_years", 2)),
        )

    # ------------------------------------------------------------------ 权限探针
    def probe(self, sample_date: str | date | None = None) -> dict[str, Any]:
        """用最小请求逐个探测 P0 接口权限（不写库）。"""
        day = str(sample_date)[:10] if sample_date else None
        if not day:
            today = date.fromisoformat(today_str())
            recent = self.calendar.trading_days_or_weekdays(
                (today - timedelta(days=10)).isoformat(), today.isoformat()
            )
            day = str(recent[-1])[:10] if recent else today.isoformat()
        period = latest_quarter_end(day)
        checks: dict[str, Callable[[], list[dict[str, Any]]]] = {
            "stock_basic_history": lambda: self.tushare.fetch_stock_basic(
                list_status="L"
            ),
            "namechange": lambda: self.tushare.fetch_namechange(
                start_date=day, end_date=day
            ),
            "new_share": lambda: self.tushare.fetch_new_share(
                start_date=day, end_date=day
            ),
            "suspend_d": lambda: self.tushare.fetch_suspend_d(trade_date=compact(day)),
            "stk_limit": lambda: self.tushare.fetch_stk_limit(trade_date=compact(day)),
            "disclosure_date": lambda: self.tushare.fetch_disclosure_date(
                end_date=period, limit=5
            ),
            "forecast": lambda: self.tushare.fetch_forecast(
                ann_date=compact(day), limit=5
            ),
            "express": lambda: self.tushare.fetch_express(period=period, limit=5),
        }
        results: dict[str, Any] = {"sample_date": day, "period": period, "datasets": {}}
        for dataset in P0_DATASETS:
            try:
                rows = checks[dataset]() or []
                results["datasets"][dataset] = {"ok": True, "rows": len(rows)}
            except Exception as exc:  # noqa: BLE001
                results["datasets"][dataset] = {
                    "ok": False,
                    "error": f"{type(exc).__name__}: {exc}"[:300],
                }
        return results


__all__ = [
    "BasicDataService",
    "STOP_ERRORS",
    "compact",
    "latest_quarter_end",
    "quarter_ends",
]
