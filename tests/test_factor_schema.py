"""Final factor-asset DDL and writer-column consistency."""

from __future__ import annotations

import re

from scripts.schemas.factor_schema import (
    ALIAS_COLUMNS,
    AUDIT_STATE_COLUMNS,
    BASIC_DATA_CATALOG_COLUMNS,
    BUILD_STATE_COLUMNS,
    LINEAGE_COLUMNS,
    REGISTRY_COLUMNS,
    VALUE_ANNOUNCE_COLUMNS,
    VALUE_DAILY_COLUMNS,
    VALUE_LATEST_COLUMNS,
    create_factor_tables,
)

_COLUMN_RE = re.compile(
    r"\b([a-z_][a-z0-9_]*)\s+(?:STRING|SYMBOL|DOUBLE|LONG|INT|TIMESTAMP|BOOLEAN|FLOAT)",
    re.IGNORECASE,
)


def _ddl_columns(ddl: str) -> list[str]:
    body = ddl.split(") TIMESTAMP(")[0]
    body = body[body.index("(") + 1 :]
    return [match.group(1).lower() for match in _COLUMN_RE.finditer(body)]


def _find(statements, table_name):
    return next(sql for sql in statements if f'"{table_name}"' in sql)


def test_factor_ddl_matches_writer_columns():
    statements = create_factor_tables(None, dry_run=True)
    assert _ddl_columns(_find(statements, "basic_data_catalog")) == BASIC_DATA_CATALOG_COLUMNS
    assert _ddl_columns(_find(statements, "custom_factor_registry")) == REGISTRY_COLUMNS
    assert _ddl_columns(_find(statements, "custom_factor_alias")) == ALIAS_COLUMNS
    assert _ddl_columns(_find(statements, "custom_factor_value_daily")) == VALUE_DAILY_COLUMNS
    assert _ddl_columns(_find(statements, "custom_factor_value_announce")) == VALUE_ANNOUNCE_COLUMNS
    assert _ddl_columns(_find(statements, "custom_factor_value_latest")) == VALUE_LATEST_COLUMNS
    assert _ddl_columns(_find(statements, "custom_factor_lineage")) == LINEAGE_COLUMNS
    assert _ddl_columns(_find(statements, "custom_factor_build_state")) == BUILD_STATE_COLUMNS
    assert _ddl_columns(_find(statements, "custom_factor_audit_state")) == AUDIT_STATE_COLUMNS


def test_factor_dedup_keys_include_designated_timestamp():
    statements = create_factor_tables(None, dry_run=True)
    for sql in statements:
        if "DEDUP UPSERT KEYS" not in sql:
            continue
        designated = re.search(r"TIMESTAMP\((\w+)\)", sql).group(1)
        keys = re.search(r"DEDUP UPSERT KEYS\(([^)]+)\)", sql).group(1)
        assert designated in keys, f"dedup key must include designated timestamp: {designated}"


def test_custom_value_tables_have_no_version_columns():
    statements = create_factor_tables(None, dry_run=True)
    for table in ("custom_factor_value_daily", "custom_factor_value_announce", "custom_factor_value_latest"):
        sql = _find(statements, table)
        assert "definition_hash" not in sql
        assert "source_version" not in sql
        assert "version " not in sql
