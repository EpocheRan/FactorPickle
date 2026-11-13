"""交易日历：补齐触发条件与数据源降级的离线测试。"""

from __future__ import annotations

from datetime import datetime

from data_interface.common.calendar_sources import default_calendar_priority
from data_interface.common.timeutil import weekday_range
from data_interface.common.trading_calendar import TradingCalendar


class FakeCalendarDB:
    """只实现 TradingCalendar 用到的两条查询 + 一条写入。"""

    def __init__(self, calendar: dict[str, bool] | None = None):
        self._calendar = dict(calendar or {})
        self.inserted: list[tuple[str, bool]] = []

    def query(self, sql: str, params=None, fetch_all: bool = True):
        text = " ".join(sql.split())
        if "MAX(ts)" in text:
            if not self._calendar:
                return None
            latest = max(self._calendar)
            return (datetime.strptime(latest, "%Y-%m-%d"),)
        if "BETWEEN" in text and "is_trading = true" in text:
            start, end = params
            return [
                (datetime.strptime(day, "%Y-%m-%d"),)
                for day in sorted(self._calendar)
                if self._calendar[day] and start <= day <= end
            ]
        raise AssertionError(f"unexpected SQL: {text}")

    def execute_values(self, prefix: str, rows) -> int:
        assert "trading_days" in prefix
        for ts, is_trading in rows:
            day = ts.strftime("%Y-%m-%d")
            self._calendar[day] = bool(is_trading)
            self.inserted.append((day, bool(is_trading)))
        return len(rows)


def _covered_db() -> FakeCalendarDB:
    return FakeCalendarDB(
        {"2024-01-02": True, "2024-01-03": True, "2024-01-04": True, "2024-01-05": True}
    )


def _recording_fetcher(calls: list[tuple[str, str]]):
    def fetcher(start_date: str, end_date: str) -> list[str]:
        calls.append((start_date, end_date))
        return weekday_range(start_date, end_date)

    return fetcher


def _failing_fetcher(message: str):
    def fetcher(start_date: str, end_date: str) -> list[str]:
        raise RuntimeError(message)

    return fetcher


def _calendar(db: FakeCalendarDB, fetchers: dict, priority: list[str]) -> TradingCalendar:
    return TradingCalendar(
        db, calendar_fetchers=fetchers, source_priority=priority
    )


def test_covered_range_never_calls_remote_source() -> None:
    calls: list[tuple[str, str]] = []
    db = _covered_db()
    calendar = _calendar(
        db,
        {"tushare": _recording_fetcher(calls), "akshare": _failing_fetcher("no")},
        ["tushare", "akshare"],
    )

    days = calendar.get_trading_days("2024-01-02", "2024-01-04")

    assert days == ["2024-01-02", "2024-01-03", "2024-01-04"]
    assert calls == []
    assert db.inserted == []


def test_fill_only_up_to_requested_end_date() -> None:
    calls: list[tuple[str, str]] = []
    db = _covered_db()
    calendar = _calendar(
        db,
        {"tushare": _recording_fetcher(calls), "akshare": _failing_fetcher("no")},
        ["tushare", "akshare"],
    )

    days = calendar.get_trading_days("2024-01-02", "2024-01-08")

    # 只补 last_covered+1 .. 请求的 end_date，而不是补到“今天”
    assert calls == [("2024-01-06", "2024-01-08")]
    assert days == [
        "2024-01-02",
        "2024-01-03",
        "2024-01-04",
        "2024-01-05",
        "2024-01-08",
    ]
    assert {day for day, _ in db.inserted} == {"2024-01-06", "2024-01-07", "2024-01-08"}
    assert calendar.last_fill_source == "tushare"
    assert calendar.last_fill_error is None


def test_source_fallback_after_primary_failure() -> None:
    calls: list[tuple[str, str]] = []
    db = _covered_db()
    calendar = _calendar(
        db,
        {
            "tushare": _failing_fetcher("Tushare 登录失败"),
            "akshare": _recording_fetcher(calls),
        },
        ["tushare", "akshare"],
    )

    days = calendar.get_trading_days("2024-01-02", "2024-01-08")

    assert calls == [("2024-01-06", "2024-01-08")]
    assert days[-1] == "2024-01-08"
    assert calendar.last_fill_source == "akshare"
    assert calendar.last_fill_error is None


def test_all_sources_failed_falls_back_to_weekdays_and_backs_off() -> None:
    calls = {"tushare": 0, "akshare": 0}

    def failing_tushare(start_date: str, end_date: str) -> list[str]:
        calls["tushare"] += 1
        raise RuntimeError("tushare down")

    def failing_akshare(start_date: str, end_date: str) -> list[str]:
        calls["akshare"] += 1
        raise RuntimeError("akshare down")

    db = _covered_db()
    calendar = _calendar(
        db,
        {"tushare": failing_tushare, "akshare": failing_akshare},
        ["tushare", "akshare"],
    )

    days = calendar.trading_days_or_weekdays("2024-01-06", "2024-01-09")

    assert days == ["2024-01-08", "2024-01-09"]
    assert calls == {"tushare": 1, "akshare": 1}
    assert calendar.last_fill_error is not None

    # 300s 退避窗口内不再重试数据源（换一个未缓存的区间验证退避生效）
    again = calendar.trading_days_or_weekdays("2024-01-06", "2024-01-10")
    assert again == ["2024-01-08", "2024-01-09", "2024-01-10"]
    assert calls == {"tushare": 1, "akshare": 1}


def test_empty_calendar_bootstraps_from_1990_in_chunks() -> None:
    calls: list[tuple[str, str]] = []
    db = FakeCalendarDB()
    calendar = _calendar(
        db,
        {"tushare": _recording_fetcher(calls), "akshare": _failing_fetcher("no")},
        ["tushare"],
    )

    days = calendar.get_trading_days("1990-01-01", "1995-01-01")

    assert calls[0][0] == "1990-01-01"
    assert calls[-1][1] == "1995-01-01"
    assert len(calls) >= 2  # 超过单次补齐跨度，按块请求
    assert days == weekday_range("1990-01-01", "1995-01-01")
    assert max(db._calendar) == "1995-01-01"
    assert len(db.inserted) == (datetime(1995, 1, 1) - datetime(1990, 1, 1)).days + 1


def test_calendar_source_priority_from_config() -> None:
    assert default_calendar_priority({"market": {"calendar_source_priority": ["Akshare", "tushare"]}}) == [
        "akshare",
        "tushare",
    ]
    assert default_calendar_priority({"market": {}}) == ["tushare", "akshare"]
    assert default_calendar_priority({}) == ["tushare", "akshare"]
