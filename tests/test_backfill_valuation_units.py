"""``acquisition.backfill`` valuation phases: canonical units and phase wiring.

The legacy ``valuation`` phase used to write Tushare's raw 万 / percent values into
columns typed as 股 / 元 / fraction.  These tests pin the canonical conversion and
the ``valuation_derived`` phase contract (local-only, dry-run, month resume).
"""

from __future__ import annotations

import math
from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest

from acquisition.backfill import PHASES, Scope
from acquisition.backfill import phases as backfill_phases


class _Frame:
    """Minimal stand-in for a Tushare DataFrame (itertuples + empty)."""

    def __init__(self, rows):
        self._rows = rows
        self.empty = not rows

    def itertuples(self, index=False):  # noqa: ARG002
        return iter(self._rows)


class _Row:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class _Api:
    def call(self, name, **kwargs):  # noqa: ARG002
        return _Frame(
            [
                _Row(
                    ts_code="600519.SH",
                    trade_date="20180102",
                    close=700.0,
                    total_share=125619.78,
                    total_mv=91885844.2788,
                    circ_mv=91885844.2788,
                    pe_ttm=31.1867,
                    pb=10.6669,
                    ps_ttm=14.7311,
                    dv_ttm=2.4316,
                ),
                _Row(
                    ts_code="000001.SZ",
                    trade_date="20180102",
                    close=13.0,
                    total_share=float("nan"),
                    total_mv=float("nan"),
                    circ_mv=float("nan"),
                    pe_ttm=7.0,
                    pb=0.9,
                    ps_ttm=1.2,
                    dv_ttm=float("nan"),
                ),
            ]
        )


@pytest.fixture()
def captured(monkeypatch):
    written: list[tuple] = []

    def _write(table, columns, rows, *, chunk=500):
        written.append((table, list(columns), list(rows), chunk))
        return len(rows)

    monkeypatch.setattr(backfill_phases, "write", _write)
    monkeypatch.setattr(backfill_phases, "trading_days", lambda start, end: ["2018-01-02"])
    return written


def test_valuation_phase_writes_canonical_units(captured):
    result = backfill_phases.valuation(_Api(), Scope("2018-01-01", "2018-01-31"))
    assert result.rows == 2
    table, columns, rows, _chunk = captured[0]
    assert table == "fin_valuation_daily"
    first = dict(zip(columns, rows[0]))
    assert first["total_share"] == pytest.approx(1_256_197_800.0)
    assert first["market_cap"] == pytest.approx(918_858_442_788.0)
    assert first["float_market_cap"] == pytest.approx(918_858_442_788.0)
    assert first["dividend_yield"] == pytest.approx(0.024316)
    assert first["close"] == 700.0
    assert first["pe_ttm"] == 31.1867
    assert first["source"] == "tushare.daily_basic"

    second = dict(zip(columns, rows[1]))
    # missing vendor values must stay null, never become NaN or 0
    assert second["total_share"] is None
    assert second["market_cap"] is None
    assert second["float_market_cap"] is None
    assert second["dividend_yield"] is None


def test_valuation_phase_dry_run_does_not_write(captured):
    result = backfill_phases.valuation(_Api(), Scope("2018-01-01", "2018-01-31", dry_run=True))
    assert result.rows == 0
    assert captured == []


def test_valuation_derived_phase_is_registered():
    assert "valuation_derived" in PHASES
    assert PHASES["valuation_derived"].tables == ("fin_valuation_daily",)
    assert "valuation" in PHASES


def test_valuation_derived_phase_is_local_only(monkeypatch):
    """The phase must never need a Tushare client."""
    from data_interface.financial import service as service_module

    calls = {}

    class _Repository:
        def valuation_field_coverage(self, start, end, fields):
            return {"rows": 0, "fields": {field: 0 for field in fields}, "coverage": {}}

    class _Service:
        repository = _Repository()

        def rebuild_valuation_derived(self, start, end, **kwargs):
            calls.update(start=start, end=end, **kwargs)
            return {
                "schema": "factor_lab.valuation_rebuild.v1",
                "start": start,
                "end": end,
                "months": [],
                "months_detail": [],
                "dry_run": kwargs.get("dry_run", False),
                "totals": {"rows_read": 0, "rows_written": 0,
                           "rows_without_statements": 0, "fields": {}},
                "before": {"rows": 0, "fields": {}, "coverage": {}},
                "after": {"rows": 0, "fields": {}, "coverage": {}},
                "predicted": {"rows": 0, "fields": {}},
                "measured": {"rows": 0, "fields": {}},
                "seconds": 0.0,
            }

    monkeypatch.setattr(service_module, "FinancialDataService", _Service)
    monkeypatch.setattr(backfill_phases, "VALUATION_DERIVED_STATE", str(Path("pytest-of-crrsw") / "state.json"))
    result = backfill_phases.valuation_derived(_Api(), Scope("2017-01-01", "2017-03-31", dry_run=True))
    assert result.phase == "valuation_derived"
    assert calls["dry_run"] is True
    assert calls["start"] == "2017-01-01"
    assert calls["end"] == "2017-03-31"
