"""Derived valuation rebuild: unit repair, PIT/TTM, idempotency, resume, dry-run.

All tests are offline: the service is driven by an in-memory statement client and
an in-memory valuation repository that mimics ``DEDUP UPSERT KEYS(ts, code, source)``.
"""

from __future__ import annotations

import copy
from datetime import datetime

import pytest

from data_interface.financial.fields import VALUATION_COLUMNS
from data_interface.financial.service import (
    FinancialDataService,
    _STATEMENT_INDEX_FIELDS,
    _STATEMENT_META_FIELDS,
    _daily_basic_from_stored_row,
    _month_ranges,
)

TS_INDEX = VALUATION_COLUMNS.index("ts")
CODE_INDEX = VALUATION_COLUMNS.index("code")
SOURCE_INDEX = VALUATION_COLUMNS.index("source")
FORMULA_INDEX = VALUATION_COLUMNS.index("formula_version")
INGESTED_INDEX = VALUATION_COLUMNS.index("ingested_at")


# ------------------------------------------------------------------ fixtures
def _income(code, period, publish, revenue, gross, ebit, ebitda, net_income):
    period_end = datetime.strptime(period, "%Y-%m-%d")
    period_type = {3: "Q1", 6: "H1", 9: "Q3", 12: "FY"}[period_end.month]
    return (
        code,
        period_end,
        datetime.strptime(publish, "%Y-%m-%d"),
        period_type,
        period_end.year,
        "consolidated",
        revenue,
        None,
        gross,
        ebit,
        ebitda,
        net_income,
    )


def _cash(code, period, publish, ocf, fcf):
    period_end = datetime.strptime(period, "%Y-%m-%d")
    period_type = {3: "Q1", 6: "H1", 9: "Q3", 12: "FY"}[period_end.month]
    return (
        code,
        period_end,
        datetime.strptime(publish, "%Y-%m-%d"),
        period_type,
        period_end.year,
        "consolidated",
        ocf,
        fcf,
    )


def _balance(code, period, publish, *, cash=50.0, total_debt=100.0, total_assets=1000.0,
             total_equity=600.0, current_assets=300.0, current_liabilities=100.0):
    period_end = datetime.strptime(period, "%Y-%m-%d")
    period_type = {3: "Q1", 6: "H1", 9: "Q3", 12: "FY"}[period_end.month]
    # order must match service._STATEMENT_INDEX_FIELDS["BS"]
    return (
        code,
        period_end,
        datetime.strptime(publish, "%Y-%m-%d"),
        period_type,
        period_end.year,
        "consolidated",
        cash,
        total_debt,
        None,
        None,
        total_assets,
        total_equity,
        None,
        current_assets,
        current_liabilities,
    )


def _full_year(code="600519", publish_fy="2018-03-25"):
    """FY2017 cumulative reports that quarterize to 100/120/110/110 revenue."""
    return (
        [
            _income(code, "2017-03-31", "2017-04-20", 100.0, 40.0, 30.0, 35.0, 15.0),
            _income(code, "2017-06-30", "2017-08-20", 220.0, 88.0, 66.0, 77.0, 33.0),
            _income(code, "2017-09-30", "2017-10-25", 330.0, 132.0, 99.0, 115.5, 49.5),
            _income(code, "2017-12-31", publish_fy, 440.0, 176.0, 132.0, 154.0, 66.0),
        ],
        [
            _cash(code, "2017-03-31", "2017-04-20", 40.0, 40.0),
            _cash(code, "2017-06-30", "2017-08-20", 80.0, 80.0),
            _cash(code, "2017-09-30", "2017-10-25", 120.0, 120.0),
            _cash(code, "2017-12-31", publish_fy, 160.0, 160.0),
        ],
        [
            _balance(code, "2017-09-30", "2017-10-25"),
            _balance(code, "2017-12-31", publish_fy),
        ],
    )


class FakeStatementClient:
    """Returns pre-canned statement rows; records the SQL it was asked for."""

    def __init__(self, income, cash, balance, *, min_ts=None):
        self.tables = {
            "fin_income_statement": income,
            "fin_cash_flow": cash,
            "fin_balance_sheet": balance,
        }
        self.min_ts = min_ts
        self.sql: list[str] = []

    def query(self, sql, params=None, fetch_all=True):
        self.sql.append(sql)
        for table, rows in self.tables.items():
            if table in sql:
                return list(rows)
        if "min(ts)" in sql:
            return [self.min_ts]
        return []


class FakeValuationRepository:
    """In-memory ``fin_valuation_daily`` with upsert-by-key semantics."""

    def __init__(self, rows):
        self.table: dict[tuple, list] = {
            (row[TS_INDEX], row[CODE_INDEX], row[SOURCE_INDEX]): list(row) for row in rows
        }
        self.written_batches = 0
        self.written_rows = 0
        self.peak_batch = 0

    def get_valuation_rows(self, start_date, end_date, *, codes=None):
        start = datetime.fromisoformat(str(start_date)[:10])
        end = datetime.fromisoformat(str(end_date)[:10])
        wanted = set(codes) if codes else None
        rows = [
            tuple(row)
            for row in self.table.values()
            if start <= row[TS_INDEX] <= end and (wanted is None or row[CODE_INDEX] in wanted)
        ]
        return sorted(rows, key=lambda item: (item[TS_INDEX], item[CODE_INDEX], item[SOURCE_INDEX]))

    def upsert_valuation_rows(self, rows, *, page_size=1000):
        payload = [list(row) for row in rows]
        self.written_batches += 1
        self.written_rows += len(payload)
        self.peak_batch = max(self.peak_batch, len(payload))
        for row in payload:
            self.table[(row[TS_INDEX], row[CODE_INDEX], row[SOURCE_INDEX])] = row
        return len(payload)

    def valuation_field_coverage(self, start_date, end_date, fields, *, codes=None):
        week = self.get_valuation_rows(start_date, end_date, codes=codes)
        counts = {
            field: sum(
                1 for row in week if row[VALUATION_COLUMNS.index(field)] is not None
            )
            for field in fields
        }
        total = len(week)
        return {
            "rows": total,
            "fields": counts,
            "coverage": {field: (counts[field] / total if total else 0.0) for field in fields},
        }


def _service(income, cash, balance, rows, *, min_ts=None):
    repository = FakeValuationRepository(rows)
    client = FakeStatementClient(income, cash, balance, min_ts=min_ts)
    service = FinancialDataService(
        db=client, repository=repository, tushare=object(), akshare=object()
    )
    return service, repository, client


def _legacy_row(ts="2018-06-29", code="600519", *, total_share=125619.78,
                market_cap=91885844.2788, dividend_yield=2.4316, close=731.46):
    row = dict.fromkeys(VALUATION_COLUMNS)
    row.update(
        ts=datetime.strptime(ts, "%Y-%m-%d"),
        code=code,
        close=close,
        total_share=total_share,
        market_cap=market_cap,
        float_market_cap=market_cap,
        pe_ttm=31.1867,
        pb_mrq=10.6669,
        ps_ttm=14.7311,
        dividend_yield=dividend_yield,
        source="tushare.daily_basic",
        formula_version="backfill.v1",
        ingested_at=datetime(2026, 11, 1, 11, 4, 28),
    )
    return [row[column] for column in VALUATION_COLUMNS]


def _v2_row(ts="2024-07-01", code="600519", *, total_share=1_256_197_800.0,
            market_cap=1_809_402_187_164.0, dividend_yield=0.034701, close=1440.38):
    row = dict.fromkeys(VALUATION_COLUMNS)
    row.update(
        ts=datetime.strptime(ts, "%Y-%m-%d"),
        code=code,
        close=close,
        total_share=total_share,
        market_cap=market_cap,
        float_market_cap=market_cap,
        pe_ttm=23.1961,
        pb_mrq=9.0044,
        ps_ttm=11.6952,
        pcf_ttm=25.65222614538864,
        pfcf=41.579816654573584,
        ev_ebitda=16.035468926468955,
        ev_ebit=16.32779482715967,
        ev_sales=11.215978699196418,
        ev_gross_profit=12.191440224527545,
        ev_fcf=39.87606657542602,
        p_nwc=9.013196919480686,
        earnings_yield=0.04469934796594192,
        dividend_yield=dividend_yield,
        source="tushare_daily_basic",
        formula_version="v2",
        ingested_at=datetime(2026, 10, 26, 11, 52, 16),
    )
    return [row[column] for column in VALUATION_COLUMNS]


# -------------------------------------------------------------------- tests
def test_daily_basic_scales_only_legacy_rows():
    legacy = _daily_basic_from_stored_row(_legacy_row())
    assert legacy["total_share"] == pytest.approx(1_256_197_800.0)
    assert legacy["market_cap"] == pytest.approx(918_858_442_788.0)
    assert legacy["float_market_cap"] == pytest.approx(918_858_442_788.0)
    assert legacy["dividend_yield"] == pytest.approx(0.024316)
    # Vendor ratios and close are unit-free / already yuan and must not move.
    assert legacy["pe_ttm"] == 31.1867
    assert legacy["pb_mrq"] == 10.6669
    assert legacy["ps_ttm"] == 14.7311
    assert legacy["close"] == 731.46

    current = _daily_basic_from_stored_row(_v2_row())
    assert current["total_share"] == pytest.approx(1_256_197_800.0)
    assert current["market_cap"] == pytest.approx(1_809_402_187_164.0)
    assert current["dividend_yield"] == pytest.approx(0.034701)


def test_rebuild_repairs_units_and_fills_derived_columns():
    income, cash, balance = _full_year()
    service, repository, _ = _service(income, cash, balance, [_legacy_row()])
    result = service.rebuild_valuation_derived("2018-06-01", "2018-06-30", chunk=100)

    assert result["totals"]["rows_read"] == 1
    assert result["totals"]["rows_written"] == 1
    row = repository.get_valuation_rows("2018-06-01", "2018-06-30")[0]
    values = dict(zip(VALUATION_COLUMNS, row))
    # unit repair
    assert values["total_share"] == pytest.approx(1_256_197_800.0)
    assert values["market_cap"] == pytest.approx(918_858_442_788.0)
    assert values["dividend_yield"] == pytest.approx(0.024316)
    # identity is preserved: source untouched, formula version upgraded
    assert values["source"] == "tushare.daily_basic"
    assert values["formula_version"] == "v2"
    # derived columns now exist and follow the documented formulas
    market_cap = values["market_cap"]
    assert values["enterprise_value"] == pytest.approx(market_cap + 100.0 - 50.0)
    assert values["ev_sales"] == pytest.approx((market_cap + 50.0) / 440.0)
    assert values["ev_gross_profit"] == pytest.approx((market_cap + 50.0) / 176.0)
    assert values["ev_ebit"] == pytest.approx((market_cap + 50.0) / 132.0)
    assert values["ev_ebitda"] == pytest.approx((market_cap + 50.0) / 154.0)
    assert values["ev_fcf"] == pytest.approx((market_cap + 50.0) / 160.0)
    assert values["pcf_ttm"] == pytest.approx(market_cap / 160.0)
    assert values["pfcf"] == pytest.approx(market_cap / 160.0)
    assert values["earnings_yield"] == pytest.approx(66.0 / market_cap)
    assert values["p_nwc"] == pytest.approx(market_cap / 200.0)
    # vendor ratios are preserved, never overwritten
    assert values["pe_ttm"] == 31.1867
    assert values["ps_ttm"] == 14.7311


def test_pit_only_uses_versions_published_on_or_before_the_trade_date():
    income, cash, balance = _full_year(publish_fy="2018-03-25")
    service, repository, _ = _service(
        income, cash, balance,
        [_legacy_row("2018-03-01"), _legacy_row("2018-03-26")],
    )
    service.rebuild_valuation_derived("2018-03-01", "2018-03-31", chunk=100)
    rows = repository.get_valuation_rows("2018-03-01", "2018-03-31")
    before, after = rows[0], rows[1]
    assert before[VALUATION_COLUMNS.index("ev_sales")] is None
    assert before[VALUATION_COLUMNS.index("enterprise_value")] is not None
    assert after[VALUATION_COLUMNS.index("ev_sales")] is not None


def test_pit_ignores_later_restatements_for_earlier_dates():
    income, cash, balance = _full_year(publish_fy="2018-03-25")
    restated = _income("600519", "2017-12-31", "2018-09-01", 999.0, 176.0, 132.0, 154.0, 66.0)
    service, repository, _ = _service(
        income + [restated], cash, balance,
        [_legacy_row("2018-06-01"), _legacy_row("2018-10-01")],
    )
    service.rebuild_valuation_derived("2018-06-01", "2018-10-31", chunk=100)
    rows = repository.get_valuation_rows("2018-06-01", "2018-10-31")
    june, october = rows[0], rows[1]
    market_cap = june[VALUATION_COLUMNS.index("market_cap")]
    assert june[VALUATION_COLUMNS.index("ev_sales")] == pytest.approx((market_cap + 50.0) / 440.0)
    assert october[VALUATION_COLUMNS.index("ev_sales")] == pytest.approx((market_cap + 50.0) / 999.0)


def test_ttm_needs_four_consecutive_quarters():
    income, cash, balance = _full_year()
    # keep only Q3 and FY: quarterize cannot produce four consecutive quarters
    partial_income = [row for row in income if row[1].month in (9, 12)]
    partial_cash = [row for row in cash if row[1].month in (9, 12)]
    service, repository, _ = _service(
        partial_income, partial_cash, balance, [_legacy_row("2018-06-01")]
    )
    service.rebuild_valuation_derived("2018-06-01", "2018-06-30", chunk=100)
    row = repository.get_valuation_rows("2018-06-01", "2018-06-30")[0]
    assert row[VALUATION_COLUMNS.index("ev_sales")] is None
    assert row[VALUATION_COLUMNS.index("pcf_ttm")] is None
    assert row[VALUATION_COLUMNS.index("earnings_yield")] is None
    # a structural gap must not silently produce a partial TTM
    assert row[VALUATION_COLUMNS.index("enterprise_value")] is not None


def test_no_enterprise_value_without_a_balance_sheet():
    income, cash, _ = _full_year()
    service, repository, _ = _service(income, cash, [], [_legacy_row("2018-06-01")])
    service.rebuild_valuation_derived("2018-06-01", "2018-06-30", chunk=100)
    row = repository.get_valuation_rows("2018-06-01", "2018-06-30")[0]
    assert row[VALUATION_COLUMNS.index("enterprise_value")] is None
    assert row[VALUATION_COLUMNS.index("ev_sales")] is None
    assert row[VALUATION_COLUMNS.index("pe_ttm")] == 31.1867


def test_statement_index_projects_period_type_and_fiscal_year():
    """The bulk path must select period_type; without it quarterize inflates TTM."""
    assert "period_type" in _STATEMENT_META_FIELDS
    assert "fiscal_year" in _STATEMENT_META_FIELDS
    income, cash, balance = _full_year()
    _, _, client = _service(income, cash, balance, [])
    service = FinancialDataService(
        db=client,
        repository=FakeValuationRepository([]),
        tushare=object(),
        akshare=object(),
    )
    index = service.load_valuation_statement_index()
    assert index["600519"]["IS"], "income statements should be indexed"
    for sql in client.sql:
        if "fin_income_statement" in sql or "fin_cash_flow" in sql or "fin_balance_sheet" in sql:
            assert "period_type" in sql
            assert "fiscal_year" in sql
            assert "publish_date IS NOT NULL" in sql


def test_rebuild_is_idempotent_and_keeps_the_row_count():
    income, cash, balance = _full_year()
    service, repository, _ = _service(
        income, cash, balance,
        [_legacy_row("2018-06-01"), _v2_row("2018-06-04", total_share=1_256_197_800.0,
                                           market_cap=1_809_402_187_164.0)],
    )
    service.rebuild_valuation_derived("2018-06-01", "2018-06-30", chunk=100)
    first = repository.get_valuation_rows("2018-06-01", "2018-06-30")
    rows_after_first = len(repository.table)

    service.rebuild_valuation_derived("2018-06-01", "2018-06-30", chunk=100)
    second = repository.get_valuation_rows("2018-06-01", "2018-06-30")

    assert len(repository.table) == rows_after_first == 2
    assert len(first) == len(second) == 2
    for left, right in zip(first, second):
        assert list(left[:INGESTED_INDEX]) == list(right[:INGESTED_INDEX])
        assert left[FORMULA_INDEX] == right[FORMULA_INDEX] == "v2"


def test_rebuild_dry_run_writes_nothing():
    income, cash, balance = _full_year()
    service, repository, _ = _service(income, cash, balance, [_legacy_row("2018-06-01")])
    before = copy.deepcopy(repository.get_valuation_rows("2018-06-01", "2018-06-30"))
    result = service.rebuild_valuation_derived(
        "2018-06-01", "2018-06-30", chunk=100, dry_run=True
    )
    assert repository.written_rows == 0
    assert repository.get_valuation_rows("2018-06-01", "2018-06-30") == before
    assert result["dry_run"] is True
    assert result["totals"]["rows_read"] == 1
    assert result["totals"]["rows_written"] == 0
    assert result["before"] == result["after"]
    # the dry run still predicts the post-rebuild coverage
    assert result["predicted"]["fields"]["ev_sales"]["coverage"] == 1.0


def test_rebuild_resumes_at_month_granularity():
    income, cash, balance = _full_year()
    service, repository, _ = _service(
        income, cash, balance,
        [_legacy_row("2018-03-01"), _legacy_row("2018-04-02")],
    )
    result = service.rebuild_valuation_derived(
        "2018-03-01", "2018-04-30", chunk=100, skip_months=["2018-03"]
    )
    assert result["months"] == ["2018-04-01"]
    assert result["skipped_months"] == ["2018-03"]
    assert result["totals"]["rows_read"] == 1
    assert repository.get_valuation_rows("2018-03-01", "2018-03-31")[0][FORMULA_INDEX] == "backfill.v1"
    assert repository.get_valuation_rows("2018-04-01", "2018-04-30")[0][FORMULA_INDEX] == "v2"


def test_month_ranges_cover_partial_months():
    assert _month_ranges("2017-01-03", "2017-03-15") == [
        ("2017-01-03", "2017-01-31"),
        ("2017-02-01", "2017-02-28"),
        ("2017-03-01", "2017-03-15"),
    ]


def test_market_cap_change_pct_uses_the_previous_row():
    income, cash, balance = _full_year()
    service, repository, _ = _service(
        income, cash, balance,
        [_legacy_row("2018-06-01"), _legacy_row("2018-06-04", market_cap=101_074_428.70668)],
    )
    service.rebuild_valuation_derived("2018-06-01", "2018-06-30", chunk=100)
    rows = repository.get_valuation_rows("2018-06-01", "2018-06-30")
    first = rows[0][VALUATION_COLUMNS.index("market_cap_change_pct")]
    second = rows[1][VALUATION_COLUMNS.index("market_cap_change_pct")]
    assert first is None
    # stored 万元 * 10,000 = yuan; the ratio is computed on repaired values
    current = 101_074_428.70668 * 10_000.0
    assert second == pytest.approx(current / 918_858_442_788.0 - 1.0)


def test_two_sources_keep_separate_keys_and_identical_values():
    income, cash, balance = _full_year()
    legacy = _legacy_row("2021-06-01")
    modern = _v2_row("2021-06-01", total_share=1_256_197_800.0, market_cap=918_858_442_788.0,
                     dividend_yield=0.024316)
    service, repository, _ = _service(income, cash, balance, [legacy, modern])
    service.rebuild_valuation_derived("2021-06-01", "2021-06-30", chunk=100)
    rows = repository.get_valuation_rows("2021-06-01", "2021-06-30")
    assert len(rows) == 2
    assert len(repository.table) == 2
    left = dict(zip(VALUATION_COLUMNS, rows[0]))
    right = dict(zip(VALUATION_COLUMNS, rows[1]))
    for field in ("ev_sales", "ev_fcf", "p_nwc", "earnings_yield", "pcf_ttm", "pfcf"):
        assert left[field] == pytest.approx(right[field])
    assert {left["source"], right["source"]} == {"tushare.daily_basic", "tushare_daily_basic"}


def test_statement_index_field_sets_cover_the_formula_inputs():
    """Every statement field the metrics core reads must be projected."""
    assert set(_STATEMENT_INDEX_FIELDS["IS"]) >= {
        "revenue", "total_revenue", "gross_profit", "ebit", "ebitda", "net_income"
    }
    assert set(_STATEMENT_INDEX_FIELDS["CF"]) >= {"operating_cash_flow", "free_cash_flow"}
    assert set(_STATEMENT_INDEX_FIELDS["BS"]) >= {
        "cash_and_cash_equivalents",
        "total_debt",
        "total_assets",
        "total_equity",
        "total_current_assets",
        "total_current_liabilities",
    }
