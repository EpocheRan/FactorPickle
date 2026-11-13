"""Final factor-asset DDL.

Basic data remains in source-domain tables.  Custom factors use the
``custom_factor_*`` tables; there is no legacy/version layer.
"""

from __future__ import annotations

import logging

from factor_engine.asset_schema import (
    BASIC_CATALOG_COLUMNS,
    CUSTOM_ALIAS_COLUMNS,
    CUSTOM_ANNOUNCE_COLUMNS,
    CUSTOM_AUDIT_STATE_COLUMNS,
    CUSTOM_BUILD_STATE_COLUMNS,
    CUSTOM_LATEST_COLUMNS,
    CUSTOM_LINEAGE_COLUMNS,
    CUSTOM_REGISTRY_COLUMNS,
    CUSTOM_VALUE_COLUMNS,
    create_asset_tables,
)

logger = logging.getLogger("Scripts.FactorSchema")

REGISTRY_COLUMNS = CUSTOM_REGISTRY_COLUMNS
ALIAS_COLUMNS = CUSTOM_ALIAS_COLUMNS
VALUE_DAILY_COLUMNS = CUSTOM_VALUE_COLUMNS
VALUE_ANNOUNCE_COLUMNS = CUSTOM_ANNOUNCE_COLUMNS
VALUE_LATEST_COLUMNS = CUSTOM_LATEST_COLUMNS
BUILD_STATE_COLUMNS = CUSTOM_BUILD_STATE_COLUMNS
AUDIT_STATE_COLUMNS = CUSTOM_AUDIT_STATE_COLUMNS
BASIC_DATA_CATALOG_COLUMNS = BASIC_CATALOG_COLUMNS
LINEAGE_COLUMNS = CUSTOM_LINEAGE_COLUMNS


def create_factor_tables(db, *, dry_run: bool = False) -> list[str]:
    return create_asset_tables(db, dry_run=dry_run)
