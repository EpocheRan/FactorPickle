"""Final factor-asset schema.

Basic data stays in the source-domain tables.  Only custom factors have a
registry and materialized values.  There is no legacy/version layer here.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("FactorEngine.AssetSchema")

CUSTOM_CUTOVER_AT = "2026-11-13T15:15:00+08:00"

CUSTOM_REGISTRY_COLUMNS = [
    "updated_at",
    "factor_id",
    "name",
    "category",
    "subcategory",
    "source_dataset",
    "source_vendor",
    "source_fields",
    "freq",
    "window",
    "lag",
    "value_kind",
    "update_mode",
    "unit",
    "direction",
    "price_basis",
    "dependencies",
    "warmup",
    "formula",
    "enabled",
    "materialization",
]

CUSTOM_ALIAS_COLUMNS = [
    "updated_at",
    "alias",
    "factor_id",
    "window",
    "params",
    "notes",
]

CUSTOM_VALUE_COLUMNS = [
    "ts",
    "code",
    "factor_id",
    "value",
    "quality",
    "computed_at",
]

CUSTOM_ANNOUNCE_COLUMNS = [
    "ts",
    "code",
    "factor_id",
    "report_period",
    "announce_date",
    "lag_days",
    "value",
    "quality",
    "computed_at",
]

CUSTOM_LATEST_COLUMNS = list(CUSTOM_VALUE_COLUMNS)

CUSTOM_BUILD_STATE_COLUMNS = [
    "updated_at",
    "job_id",
    "dataset",
    "factor_ids",
    "window_start",
    "window_end",
    "cursor",
    "status",
    "attempts",
    "retry_at",
    "rows_written",
    "last_error",
    "payload",
]

CUSTOM_AUDIT_STATE_COLUMNS = [
    "updated_at",
    "audit_id",
    "dataset",
    "factor_ids",
    "window_start",
    "window_end",
    "status",
    "checked",
    "failed",
    "max_abs_diff",
    "max_rel_diff",
    "details",
    "payload",
]

BASIC_CATALOG_COLUMNS = [
    "updated_at",
    "basic_id",
    "name",
    "category",
    "source",
    "source_table",
    "source_column",
    "data_type",
    "freq",
    "pit",
    "unit",
    "description",
    "status",
]

CUSTOM_LINEAGE_COLUMNS = [
    "updated_at",
    "factor_id",
    "parent_asset_id",
    "parent_class",
    "relation",
]


def ddl() -> list[str]:
    return [
        """
        CREATE TABLE IF NOT EXISTS "basic_data_catalog" (
            updated_at TIMESTAMP,
            basic_id SYMBOL,
            name STRING,
            category SYMBOL,
            source SYMBOL,
            source_table SYMBOL,
            source_column SYMBOL,
            data_type SYMBOL,
            freq SYMBOL,
            pit SYMBOL,
            unit SYMBOL,
            description STRING,
            status SYMBOL
        ) TIMESTAMP(updated_at) PARTITION BY YEAR WAL
        DEDUP UPSERT KEYS(updated_at, basic_id)
        """,
        """
        CREATE TABLE IF NOT EXISTS "custom_factor_registry" (
            updated_at TIMESTAMP,
            factor_id SYMBOL,
            name STRING,
            category SYMBOL,
            subcategory SYMBOL,
            source_dataset SYMBOL,
            source_vendor SYMBOL,
            source_fields STRING,
            freq SYMBOL,
            window INT,
            lag INT,
            value_kind SYMBOL,
            update_mode SYMBOL,
            unit SYMBOL,
            direction SYMBOL,
            price_basis SYMBOL,
            dependencies STRING,
            warmup INT,
            formula STRING,
            enabled BOOLEAN,
            materialization SYMBOL
        ) TIMESTAMP(updated_at) PARTITION BY YEAR WAL
        DEDUP UPSERT KEYS(updated_at, factor_id)
        """,
        """
        CREATE TABLE IF NOT EXISTS "custom_factor_alias" (
            updated_at TIMESTAMP,
            alias SYMBOL,
            factor_id SYMBOL,
            window INT,
            params STRING,
            notes STRING
        ) TIMESTAMP(updated_at) PARTITION BY YEAR WAL
        DEDUP UPSERT KEYS(updated_at, alias)
        """,
        """
        CREATE TABLE IF NOT EXISTS "custom_factor_value_daily" (
            ts TIMESTAMP,
            code SYMBOL,
            factor_id SYMBOL,
            value DOUBLE,
            quality SYMBOL,
            computed_at TIMESTAMP
        ) TIMESTAMP(ts) PARTITION BY MONTH WAL
        DEDUP UPSERT KEYS(ts, code, factor_id)
        """,
        """
        CREATE TABLE IF NOT EXISTS "custom_factor_value_announce" (
            ts TIMESTAMP,
            code SYMBOL,
            factor_id SYMBOL,
            report_period TIMESTAMP,
            announce_date TIMESTAMP,
            lag_days INT,
            value DOUBLE,
            quality SYMBOL,
            computed_at TIMESTAMP
        ) TIMESTAMP(ts) PARTITION BY YEAR WAL
        DEDUP UPSERT KEYS(ts, code, factor_id, report_period)
        """,
        """
        CREATE TABLE IF NOT EXISTS "custom_factor_value_latest" (
            ts TIMESTAMP,
            code SYMBOL,
            factor_id SYMBOL,
            value DOUBLE,
            quality SYMBOL,
            computed_at TIMESTAMP
        ) TIMESTAMP(ts) PARTITION BY YEAR WAL
        DEDUP UPSERT KEYS(ts, code, factor_id)
        """,
        """
        CREATE TABLE IF NOT EXISTS "custom_factor_lineage" (
            updated_at TIMESTAMP,
            factor_id SYMBOL,
            parent_asset_id SYMBOL,
            parent_class SYMBOL,
            relation SYMBOL
        ) TIMESTAMP(updated_at) PARTITION BY YEAR WAL
        DEDUP UPSERT KEYS(updated_at, factor_id, parent_asset_id)
        """,
        """
        CREATE TABLE IF NOT EXISTS "custom_factor_build_state" (
            updated_at TIMESTAMP,
            job_id SYMBOL,
            dataset SYMBOL,
            factor_ids STRING,
            window_start TIMESTAMP,
            window_end TIMESTAMP,
            cursor TIMESTAMP,
            status SYMBOL,
            attempts INT,
            retry_at TIMESTAMP,
            rows_written LONG,
            last_error STRING,
            payload STRING
        ) TIMESTAMP(updated_at) PARTITION BY YEAR WAL
        DEDUP UPSERT KEYS(updated_at, job_id)
        """,
        """
        CREATE TABLE IF NOT EXISTS "custom_factor_audit_state" (
            updated_at TIMESTAMP,
            audit_id SYMBOL,
            dataset SYMBOL,
            factor_ids STRING,
            window_start TIMESTAMP,
            window_end TIMESTAMP,
            status SYMBOL,
            checked LONG,
            failed LONG,
            max_abs_diff DOUBLE,
            max_rel_diff DOUBLE,
            details STRING,
            payload STRING
        ) TIMESTAMP(updated_at) PARTITION BY YEAR WAL
        DEDUP UPSERT KEYS(updated_at, audit_id)
        """,
    ]


def asset_view_ddl() -> str:
    return """
    CREATE VIEW IF NOT EXISTS "asset_catalog" AS
    SELECT basic_id AS asset_id, 'basic' AS asset_class, 'data' AS asset_type,
           name, category, source, source_table AS source_table,
           source_column AS source_column, status, updated_at
    FROM basic_data_catalog
    UNION ALL
    SELECT factor_id AS asset_id, 'custom' AS asset_class, 'factor' AS asset_type,
           name, category, source_dataset AS source, NULL AS source_table,
           NULL AS source_column, CASE WHEN enabled THEN 'active' ELSE 'inactive' END AS status,
           updated_at
    FROM custom_factor_registry
    """


def create_asset_tables(db, *, dry_run: bool = False) -> list[str]:
    statements = ddl()
    for sql in statements:
        if dry_run:
            logger.info("[dry-run] %s", sql.strip().splitlines()[0])
        else:
            db.execute_ddl(sql)
    view_sql = asset_view_ddl()
    if dry_run:
        logger.info("[dry-run] %s", view_sql.strip().splitlines()[0])
    else:
        try:
            db.execute_ddl(view_sql)
        except Exception as exc:  # noqa: BLE001
            logger.warning("asset_catalog view not created: %s", exc)
    return statements
