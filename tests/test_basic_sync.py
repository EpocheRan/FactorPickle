"""P0 基础资料离线测试：字段映射、分页批次、断点续跑与权限阻断。

全部使用 fake 数据源/仓储，不联网、不连 QuestDB。
"""

from __future__ import annotations

import json
from datetime import datetime

import pandas as pd
import pytest

from acquisition.datasets import AShareSyncService
from data_interface.basic.normalizer import (
    map_disclosure_date,
    map_express,
    map_forecast,
    map_namechange,
    map_new_share,
    map_stk_limit,
    map_stock_basic,
    map_suspend_d,
)
from data_interface.basic.service import (
    BasicDataService,
    latest_quarter_end,
    quarter_ends,
)
from data_interface.common.exceptions import PermissionDeniedError
from data_interface.common.timeutil import today_str
from data_interface.financial.sources.tushare_source import TushareSource


# ------------------------------------------------------------------ 映射
def test_map_stock_basic_merges_statuses_and_derives_st():
    snapshot = datetime(2026, 11, 13)
    rows = [
        {
            "ts_code": "600519.SH",
            "name": "贵州茅台",
            "fullname": "贵州茅台酒股份有限公司",
            "market": "主板",
            "list_status": "L",
            "list_date": "20010827",
            "industry": "白酒",
            "area": "贵州",
            "is_hs": "H",
            "act_name": "中国贵州茅台酒厂（集团）有限责任公司",
            "act_ent_type": "地方国企",
        },
        {
            "ts_code": "920001.BJ",
            "name": "*ST测试",
            "market": "北交所",
            "list_status": "P",
            "list_date": "20240101",
        },
        # 同一代码的旧退市记录不能覆盖在市记录
        {
            "ts_code": "600519.SH",
            "name": "旧茅台",
            "list_status": "D",
            "list_date": "19990101",
            "delist_date": "20010826",
        },
    ]
    mapped = map_stock_basic(rows, snapshot)
    by_code = {row["code"]: row for row in mapped}
    assert len(mapped) == 2
    assert by_code["600519"]["list_status"] == "L"
    assert by_code["600519"]["is_st"] is False
    assert by_code["600519"]["exchange"] == "SSE"
    assert by_code["600519"]["board"] == "MAIN"
    assert by_code["600519"]["list_date"] == datetime(2001, 8, 27)
    assert by_code["920001"]["exchange"] == "BSE"
    assert by_code["920001"]["board"] == "BSE"
    assert by_code["920001"]["is_st"] is True
    assert all(row["ts"] == snapshot for row in mapped)


def test_map_namechange_uses_effective_start_date():
    rows = [
        {
            "ts_code": "600519.SH",
            "name": "*ST测试",
            "start_date": "20180101",
            "end_date": "20200101",
            "ann_date": "20171220",
            "change_reason": "ST",
        },
        # start_date 缺失时退回收公告日，不丢记录
        {"ts_code": "000001.SZ", "name": "只有公告日", "ann_date": "20200101"},
    ]
    mapped = map_namechange(rows)
    assert len(mapped) == 2
    row = mapped[0]
    assert row["code"] == "600519"
    assert row["ts"] == datetime(2018, 1, 1)
    assert row["end_date"] == datetime(2020, 1, 1)
    assert row["ann_date"] == datetime(2017, 12, 20)
    assert row["change_reason"] == "ST"
    assert mapped[1]["ts"] == datetime(2020, 1, 1)


def test_map_new_share_falls_back_to_issue_date():
    mapped = map_new_share(
        [
            {
                "ts_code": "301999.SZ",
                "sub_code": "301999",
                "name": "测试股份",
                "ipo_date": None,
                "issue_date": "20240105",
                "price": "19.18",
                "pe": float("nan"),
            }
        ]
    )
    assert len(mapped) == 1
    assert mapped[0]["ts"] == datetime(2024, 1, 5)
    assert mapped[0]["price"] == pytest.approx(19.18)
    assert mapped[0]["pe"] is None


def test_map_suspend_d_keeps_suspend_and_resume_rows():
    mapped = map_suspend_d(
        [
            {"ts_code": "600519.SH", "trade_date": "20260105", "suspend_type": "S", "suspend_timing": "09:30"},
            {"ts_code": "600519.SH", "trade_date": "20260105", "suspend_type": "R", "suspend_timing": "10:00"},
        ]
    )
    assert [row["suspend_type"] for row in mapped] == ["S", "R"]
    assert all(row["ts"] == datetime(2026, 1, 5) for row in mapped)


def test_map_stk_limit_coerces_numbers_and_nan():
    mapped = map_stk_limit(
        [
            {"ts_code": "600519.SH", "trade_date": "20260105", "up_limit": "1597.2", "down_limit": float("nan")},
            {"ts_code": "000001", "trade_date": "20260105", "up_limit": None, "down_limit": 9.9},
        ]
    )
    assert mapped[0]["up_limit"] == pytest.approx(1597.2)
    assert mapped[0]["down_limit"] is None
    assert mapped[1]["code"] == "000001"


def test_map_disclosure_date_uses_report_period_as_ts():
    mapped = map_disclosure_date(
        [
            {
                "ts_code": "600519.SH",
                "ann_date": "20260115",
                "end_date": "20251231",
                "pre_date": "20260410",
                "actual_date": None,
            }
        ]
    )
    row = mapped[0]
    assert row["ts"] == datetime(2025, 12, 31)
    assert row["ann_date"] == datetime(2026, 1, 15)
    assert row["pre_date"] == datetime(2026, 4, 10)
    assert row["actual_date"] is None


def test_map_forecast_dedupes_revisions_by_update_flag():
    base = {
        "ts_code": "600519.SH",
        "ann_date": "20260130",
        "end_date": "20251231",
        "type": "预增",
        "net_profit_min": 1.0,
        "update_flag": "0",
    }
    revised = {**base, "net_profit_min": 2.0, "update_flag": "1"}
    mapped = map_forecast([base, revised])
    assert len(mapped) == 1
    assert mapped[0]["report_type"] == "预增"
    assert mapped[0]["net_profit_min"] == pytest.approx(2.0)
    assert mapped[0]["update_flag"] == "1"


def test_map_express_keeps_latest_revision():
    base = {
        "ts_code": "600519.SH",
        "ann_date": "20260201",
        "end_date": "20251231",
        "revenue": 100.0,
        "update_flag": "0",
    }
    revised = {**base, "revenue": 120.0, "update_flag": "1"}
    mapped = map_express([base, revised])
    assert len(mapped) == 1
    assert mapped[0]["revenue"] == pytest.approx(120.0)
    assert mapped[0]["end_date"] == datetime(2025, 12, 31)


# ------------------------------------------------------------------ 报告期
def test_quarter_ends_includes_prior_year_annual_report():
    periods = quarter_ends("2017-01-01", "2017-12-31")
    assert periods[0] == "20161231"
    assert "20170331" in periods
    assert periods[-1] == "20171231"


def test_latest_quarter_end():
    assert latest_quarter_end("2026-11-07") == "20260930"
    assert latest_quarter_end("2026-01-02") == "20251231"


# ------------------------------------------------------------------ Tushare 字段
def _capturing_source() -> tuple[TushareSource, dict]:
    captured: dict = {}

    class FakePro:
        def __getattr__(self, name):
            return lambda **kwargs: None

    source = TushareSource.__new__(TushareSource)
    source._pro = FakePro()
    source._call = lambda interface, func, **kwargs: captured.update(
        {"interface": interface, **kwargs}
    ) or []
    return source, captured


def test_tushare_p0_fetchers_send_explicit_fields():
    source, captured = _capturing_source()

    source.fetch_namechange(start_date="2024-01-01", end_date="2024-12-31")
    assert captured["interface"] == "namechange"
    assert captured["start_date"] == "20240101"
    assert "change_reason" in captured["fields"]

    source.fetch_new_share(start_date="2024-01-01", end_date="2024-01-10")
    assert captured["interface"] == "new_share"
    assert "ipo_date" in captured["fields"]

    source.fetch_suspend_d(trade_date="2024-01-02", suspend_type="S")
    assert captured["interface"] == "suspend_d"
    assert captured["trade_date"] == "20240102"
    assert captured["suspend_type"] == "S"
    assert "suspend_timing" in captured["fields"]

    source.fetch_stk_limit(trade_date="2024-01-02")
    assert captured["interface"] == "stk_limit"
    assert "up_limit" in captured["fields"]

    source.fetch_disclosure_date(end_date="2024-03-31")
    assert captured["interface"] == "disclosure_date"
    assert captured["end_date"] == "20240331"
    assert "pre_date" in captured["fields"]

    source.fetch_forecast(ann_date="2024-01-31", offset=2000, limit=2000)
    assert captured["interface"] == "forecast"
    assert captured["ann_date"] == "20240131"
    assert captured["offset"] == 2000
    assert "update_flag" in captured["fields"]

    source.fetch_express(period="2023-12-31", offset=0, limit=2000)
    assert captured["interface"] == "express"
    assert captured["period"] == "20231231"
    assert "total_hldr_eqy_exc_min_int" in captured["fields"]


def test_tushare_stock_basic_requests_actor_fields():
    source, captured = _capturing_source()
    source.fetch_stock_basic(list_status="L")
    for column in ("list_status", "delist_date", "act_name", "act_ent_type"):
        assert column in captured["fields"]


def test_tushare_p0_fetcher_converts_fake_frame_to_records(monkeypatch):
    """真实调用路径：pro.xxx 返回 DataFrame → ``_call`` 转成 list[dict]。"""

    class FakePro:
        @staticmethod
        def suspend_d(**kwargs):
            return pd.DataFrame(
                [
                    {
                        "ts_code": "600519.SH",
                        "trade_date": "20240102",
                        "suspend_timing": None,
                        "suspend_type": "S",
                    }
                ]
            )

    source = TushareSource.__new__(TushareSource)
    source._pro = FakePro()
    source.limiter = type("Limiter", (), {"acquire": lambda self: None})()
    source.max_retries = 1
    source.backoff = 0.0
    rows = source.fetch_suspend_d(trade_date="20240102")
    assert rows == [
        {
            "ts_code": "600519.SH",
            "trade_date": "20240102",
            "suspend_timing": None,
            "suspend_type": "S",
        }
    ]


# ------------------------------------------------------------------ 服务分页
class FakeRepo:
    def __init__(self):
        self.batches: list[tuple[str, list[dict]]] = []
        self.latest = None

    def upsert(self, dataset, rows):
        rows = list(rows)
        self.batches.append((dataset, rows))
        return len(rows)

    def latest_ts(self, dataset):
        return self.latest


class FakeCalendar:
    def __init__(self, days):
        self.days = days

    def trading_days_or_weekdays(self, start, end):
        return [day for day in self.days if str(start) <= day <= str(end)]


class FakeTushare:
    def __init__(self, *, basic=None, suspend=None, limits=None, disclosure=None,
                 forecast=None, express=None, namechange=None, new_share=None):
        self.basic = basic or {}
        self.suspend = suspend or {}
        self.limits = limits or {}
        self.disclosure = disclosure or {}
        self.forecast = forecast or {}
        self.express = express or {}
        self.namechange = namechange or {}
        self.new_share = new_share or {}
        self.calls: list[tuple] = []

    def fetch_stock_basic(self, list_status=None, code=None, **kwargs):
        self.calls.append(("stock_basic", list_status))
        return list(self.basic.get(list_status, []))

    def fetch_namechange(self, start_date=None, end_date=None, offset=0, limit=2000, **kwargs):
        self.calls.append(("namechange", start_date))
        return list(self.namechange.get(start_date, []))

    def fetch_new_share(self, start_date=None, end_date=None, offset=0, limit=2000, **kwargs):
        self.calls.append(("new_share", start_date))
        return list(self.new_share.get(start_date, []))

    def fetch_suspend_d(self, trade_date=None, **kwargs):
        self.calls.append(("suspend_d", trade_date))
        return list(self.suspend.get(trade_date, []))

    def fetch_stk_limit(self, trade_date=None, **kwargs):
        self.calls.append(("stk_limit", trade_date))
        return list(self.limits.get(trade_date, []))

    def fetch_disclosure_date(self, end_date=None, offset=0, limit=2000, **kwargs):
        self.calls.append(("disclosure_date", end_date))
        return list(self.disclosure.get(end_date, []))

    def fetch_forecast(self, ann_date=None, offset=0, limit=2000, **kwargs):
        self.calls.append(("forecast", ann_date))
        return list(self.forecast.get(ann_date, []))

    def fetch_express(self, period=None, offset=0, limit=2000, **kwargs):
        self.calls.append(("express", period))
        return list(self.express.get(period, []))


def _service(tushare, calendar_days, repo=None):
    repo = repo or FakeRepo()
    service = BasicDataService(
        repository=repo,
        tushare=tushare,
        trading_calendar=FakeCalendar(calendar_days),
        config={},
    )
    return service, repo


def test_sync_days_batches_and_resumes_cursor():
    days = ["2026-11-05", "2026-11-06", "2026-11-07"]
    tushare = FakeTushare(
        suspend={
            "20261105": [{"ts_code": "600519.SH", "trade_date": "20261105", "suspend_type": "S"}],
            "20261106": [{"ts_code": "000001.SZ", "trade_date": "20261106", "suspend_type": "R"}],
            "20261107": [{"ts_code": "300750.SZ", "trade_date": "20261107", "suspend_type": "S"}],
        }
    )
    service, repo = _service(tushare, days)

    first = service.sync_days("suspend_d", "2026-11-05", "2026-11-07", max_days=2)
    assert first["done"] is False
    assert first["next_cursor"] == "2026-11-07"
    assert first["rows_written"] == 2
    assert first["processed_days"] == ["2026-11-05", "2026-11-06"]

    second = service.sync_days(
        "suspend_d", "2026-11-05", "2026-11-07", max_days=2, cursor=first["next_cursor"]
    )
    assert second["done"] is True
    assert second["next_cursor"] is None
    assert second["rows_written"] == 1
    assert repo.batches[-1][0] == "suspend_d"


def test_empty_recent_day_is_pending_but_old_empty_day_is_not():
    today = today_str()
    tushare = FakeTushare(suspend={})
    service, repo = _service(tushare, [today, "2020-01-02"])

    recent = service.sync_days("suspend_d", today, today, max_days=1)
    assert recent["empty_days"] == [today]
    assert recent["pending"] == [today]

    old = service.sync_days("suspend_d", "2020-01-02", "2020-01-02", max_days=1)
    assert old["empty_days"] == ["2020-01-02"]
    assert old["pending"] == []
    assert repo.batches == []


def test_stk_limit_retries_historical_empty_days():
    """stk_limit 每个交易日都应有数据，历史空日也要记 pending（受尝试上限约束）。"""
    tushare = FakeTushare(limits={})
    service, _ = _service(tushare, ["2020-01-02"])
    result = service.sync_days("stk_limit", "2020-01-02", "2020-01-02", max_days=1)
    assert result["done"] is True
    assert result["empty_days"] == ["2020-01-02"]
    assert result["pending"] == ["2020-01-02"]


def test_sync_periods_batches_and_resumes():
    periods = quarter_ends("2025-01-01", "2026-09-30")
    assert len(periods) == 8
    disclosure = {
        period: [
            {
                "ts_code": "600519.SH",
                "ann_date": "20260115",
                "end_date": period,
                "pre_date": "20260410",
            }
        ]
        for period in periods
    }
    tushare = FakeTushare(disclosure=disclosure)
    service, repo = _service(tushare, [])

    first = service.sync_periods(
        "disclosure_date", "2025-01-01", "2026-09-30", max_periods=4
    )
    assert first["done"] is False
    assert first["next_cursor"] == "20251231"
    assert first["rows_written"] == 4

    second = service.sync_periods(
        "disclosure_date",
        "2025-01-01",
        "2026-09-30",
        max_periods=4,
        cursor=first["next_cursor"],
    )
    assert second["done"] is True
    assert second["rows_written"] == 4


def test_sync_range_batches_by_year_and_resumes():
    tushare = FakeTushare(
        namechange={
            "2017-01-01": [{"ts_code": "600519.SH", "name": "A", "start_date": "20170105", "ann_date": "20170101"}],
            "2018-01-01": [{"ts_code": "600519.SH", "name": "B", "start_date": "20180105", "ann_date": "20180101"}],
            "2019-01-01": [{"ts_code": "000001.SZ", "name": "C", "start_date": "20190105", "ann_date": "20190101"}],
        }
    )
    service, repo = _service(tushare, [])

    first = service.sync_range("namechange", "2017-01-01", "2019-12-31", max_years=2)
    assert first["done"] is False
    assert first["next_cursor"] == "2019-01-01"
    assert first["rows_written"] == 2

    second = service.sync_range(
        "namechange", "2017-01-01", "2019-12-31", max_years=2, cursor=first["next_cursor"]
    )
    assert second["done"] is True
    assert second["rows_written"] == 1


def test_stock_basic_snapshot_writes_merged_statuses():
    tushare = FakeTushare(
        basic={
            "L": [
                {"ts_code": "600519.SH", "name": "贵州茅台", "list_status": "L", "list_date": "20010827"},
                {"ts_code": "000001.SZ", "name": "平安银行", "list_status": "L", "list_date": "19910403"},
            ],
            "P": [],
            "D": [
                {"ts_code": "600519.SH", "name": "旧茅台", "list_status": "D", "delist_date": "20010826"}
            ],
        }
    )
    service, repo = _service(tushare, [])
    result = service.sync_stock_basic_history()
    assert result["done"] is True
    assert result["rows_written"] == 2
    assert result["status_counts"] == {"L": 2, "P": 0, "D": 1}
    dataset, rows = repo.batches[-1]
    assert dataset == "stock_basic_history"
    assert {row["code"] for row in rows} == {"600519", "000001"}
    assert all(row["list_status"] == "L" for row in rows)


# ------------------------------------------------------------------ 续跑/阻断
class FakeState:
    def __init__(self, saved=None):
        self.saved = dict(saved or {})

    def get(self, task, dataset):
        return self.saved.get(dataset)

    def save(self, task, dataset, **kwargs):
        self.saved[dataset] = kwargs


class FakeBasicService:
    def __init__(self, result=None, error=None):
        self.calls: list[dict] = []
        self.result = result or {}
        self.error = error

    def latest_ts(self, dataset):
        return None

    def sync(self, dataset, start, end, **kwargs):
        self.calls.append(
            {"dataset": dataset, "start": start, "end": end, **kwargs}
        )
        if self.error is not None:
            raise self.error
        return dict(self.result)


def _sync_service(state, basic_service):
    service = object.__new__(AShareSyncService)
    service.settings = {}
    service.state = state
    service._basic_service = basic_service
    return service


def test_sync_wrapper_saves_cursor_and_pending():
    state = FakeState()
    basic = FakeBasicService(
        result={
            "dataset": "forecast",
            "done": False,
            "next_cursor": "2026-11-07",
            "rows_written": 10,
            "processed_days": ["2026-11-05", "2026-11-06"],
            "pending": [],
            "total_days": 3,
        }
    )
    service = _sync_service(state, basic)

    result = service.sync_forecast(start_date="2026-11-05", end_date="2026-11-07")
    assert result["done"] is False
    saved = state.saved["forecast"]
    assert saved["cursor"] == "2026-11-07"
    assert saved["status"] == "RUNNING"
    assert saved["payload"]["start_date"] == "2026-11-05"
    assert basic.calls[0]["start"] == "2026-11-05"


def test_sync_wrapper_resumes_from_saved_cursor():
    state = FakeState(
        {
            "forecast": {
                "cursor": "2026-11-06",
                "status": "RUNNING",
                "payload": json.dumps({"start_date": "2026-11-05", "end_date": "2026-11-07"}),
            }
        }
    )
    basic = FakeBasicService(
        result={"done": True, "next_cursor": None, "rows_written": 1, "total_days": 1}
    )
    service = _sync_service(state, basic)

    service.sync_forecast(start_date="2026-11-05", end_date="2026-11-07")
    assert basic.calls[0]["cursor"] == "2026-11-06"
    assert basic.calls[0]["start"] == "2026-11-05"


def test_sync_wrapper_skips_done_window():
    state = FakeState(
        {
            "stk_limit": {
                "cursor": None,
                "status": "DONE",
                "payload": json.dumps({"start_date": "2017-01-01", "end_date": "2026-11-07"}),
            }
        }
    )
    basic = FakeBasicService()
    service = _sync_service(state, basic)

    result = service.sync_stk_limit(start_date="2017-01-01", end_date="2026-11-07")
    assert result["skipped"] is True
    assert basic.calls == []


def test_permission_blocked_is_recorded_and_does_not_raise():
    state = FakeState()
    basic = FakeBasicService(
        error=PermissionDeniedError("积分不足", interface="forecast", required_points=5000)
    )
    service = _sync_service(state, basic)

    result = service.sync_forecast(start_date="2017-01-01", end_date="2026-11-07")
    assert result["blocked"] is True
    assert result["done"] is True
    assert state.saved["forecast"]["status"] == "BLOCKED"


def test_probe_reports_blocked_dataset_without_raising():
    class ProbeTushare(FakeTushare):
        def fetch_forecast(self, ann_date=None, offset=0, limit=2000, **kwargs):
            raise PermissionDeniedError("积分不足", interface="forecast")

    service, _ = _service(ProbeTushare(), ["2026-11-06"])
    result = service.probe("2026-11-06")
    assert result["datasets"]["suspend_d"]["ok"] is True
    assert result["datasets"]["forecast"]["ok"] is False
    assert "积分不足" in result["datasets"]["forecast"]["error"]


# ------------------------------------------------------------------ 写入分页
def test_repository_uses_smaller_pages_for_long_text_dataset():
    """forecast 的长文本单行可达数 KB，一次 1000 行会被 QuestDB 断连。"""
    from data_interface.basic.repository import BasicRepository

    class FakeDB:
        def __init__(self):
            self.pages: list[int] = []

        def execute_values(self, sql, rows, page_size=1000):
            self.pages.append(page_size)
            return len(rows)

    db = FakeDB()
    repo = BasicRepository(db=db, config={})
    repo.upsert("forecast", [{"ts": datetime(2024, 1, 31), "code": "600519"}])
    repo.upsert("stk_limit", [{"ts": datetime(2024, 1, 31), "code": "600519"}])
    assert db.pages == [200, 1000]
