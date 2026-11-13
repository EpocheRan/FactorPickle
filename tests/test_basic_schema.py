"""P0 基础资料表结构测试：DDL 与写入列必须一致，且都参与幂等去重。"""

from __future__ import annotations

import re

from acquisition.datasets import AShareSyncService
from acquisition.orchestrator import DATASET_ORDER
from acquisition.syncers import REAL_SYNCERS
from data_interface.basic.models import (
    DATASET_COLUMNS,
    DATASET_TABLE,
    P0_DATASETS,
)
from scripts.maintenance.init_database import SUB_SCRIPTS
from scripts.schemas.basic_schema import create_basic_tables

_COLUMN_RE = re.compile(
    r"\b([a-z_][a-z0-9_]*)\s+(?:STRING|SYMBOL|DOUBLE|LONG|INT|TIMESTAMP|BOOLEAN|VARCHAR)",
    re.IGNORECASE,
)


def _ddl_columns(ddl: str) -> list[str]:
    body = ddl.split(") TIMESTAMP(")[0]
    body = body[body.index("(") + 1 :]
    return [match.group(1).lower() for match in _COLUMN_RE.finditer(body)]


def _find(statements, table_name):
    return next(sql for sql in statements if f'"{table_name}"' in sql)


def test_every_p0_table_ddl_matches_writer_columns():
    statements = create_basic_tables(None, dry_run=True)
    assert len(statements) == len(P0_DATASETS)
    for dataset in P0_DATASETS:
        table = DATASET_TABLE[dataset]
        assert _ddl_columns(_find(statements, table)) == DATASET_COLUMNS[dataset], table


def test_p0_tables_are_wal_with_dedup_on_designated_timestamp():
    statements = create_basic_tables(None, dry_run=True)
    for sql in statements:
        assert "WAL" in sql
        assert "DEDUP UPSERT KEYS" in sql
        assert "TIMESTAMP(ts) PARTITION BY" in sql
        designated = re.search(r"TIMESTAMP\((\w+)\)", sql).group(1)
        keys = re.search(r"DEDUP UPSERT KEYS\(([^)]+)\)", sql).group(1)
        assert designated in keys, sql


def test_suspend_d_keeps_both_suspend_types():
    """同一天同一代码可能同时有 S/R，主键必须带上 suspend_type。"""
    statements = create_basic_tables(None, dry_run=True)
    ddl = _find(statements, "suspend_d")
    assert "DEDUP UPSERT KEYS(ts, code, suspend_type)" in ddl


def test_forecast_key_includes_report_type():
    ddl = _find(create_basic_tables(None, dry_run=True), "forecast")
    assert "DEDUP UPSERT KEYS(ts, code, end_date, report_type)" in ddl


def test_basic_tables_are_registered_in_init_database():
    assert SUB_SCRIPTS["basic"] is create_basic_tables


def test_p0_datasets_are_registered_in_acquisition():
    for dataset in P0_DATASETS:
        assert dataset in DATASET_ORDER
        assert dataset in REAL_SYNCERS
        assert hasattr(AShareSyncService, f"sync_{dataset}")
        assert DATASET_TABLE[dataset] == dataset
    assert set(REAL_SYNCERS) == set(DATASET_ORDER)


def test_stock_basic_history_does_not_touch_current_profile_table():
    statements = create_basic_tables(None, dry_run=True)
    joined = "\n".join(statements)
    assert '"stock_basic_info"' not in joined
    assert '"fin_disclosure_calendar"' not in joined
    assert '"market_suspension"' not in joined
