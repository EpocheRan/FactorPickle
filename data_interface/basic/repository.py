"""P0 基础资料的 QuestDB 读写。

写入走 ``INSERT ... VALUES %s`` + ``execute_values``（每批 1000 行），
配合表的 ``WAL + DEDUP UPSERT KEYS`` 天然幂等；重复回补只会覆盖同一主键。
"""

from __future__ import annotations

import logging
import math
from datetime import date, datetime, time
from typing import Any, Iterable

import pandas as pd

from data_interface.common.config import get_config
from data_interface.common.questdb_client import QuestDBClient

from .models import (
    DATASET_COLUMNS,
    DATASET_PAGE_ROWS,
    DATASET_TABLE,
    DEFAULT_PAGE_ROWS,
    P0_DATASETS,
)

logger = logging.getLogger("DataInterface.Basic.Repository")


def _clean(value: Any) -> Any:
    """把 pandas / numpy 值转成 psycopg2 可绑定的原生值。"""
    if value is None:
        return None
    if isinstance(value, (pd.Timestamp, datetime)):
        return pd.Timestamp(value).to_pydatetime()
    if isinstance(value, date):
        return datetime.combine(value, time.min)
    if hasattr(value, "item") and not isinstance(value, (str, bytes)):
        try:
            value = value.item()
        except (AttributeError, ValueError):
            pass
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    return value


class BasicRepository:
    def __init__(
        self,
        db: QuestDBClient | None = None,
        config: dict[str, Any] | None = None,
    ):
        self.config = config or get_config()
        self.db = db or QuestDBClient(self.config)

    # ------------------------------------------------------------------ 写
    def upsert(self, dataset: str, rows: Iterable[dict[str, Any]]) -> int:
        if dataset not in DATASET_COLUMNS:
            raise ValueError(f"未知 P0 数据集: {dataset!r}")
        columns = DATASET_COLUMNS[dataset]
        payload = [
            tuple(_clean(row.get(column)) for column in columns) for row in rows
        ]
        if not payload:
            return 0
        quoted = ", ".join(f'"{column}"' for column in columns)
        sql = f'INSERT INTO "{DATASET_TABLE[dataset]}" ({quoted}) VALUES %s'
        page_size = int(DATASET_PAGE_ROWS.get(dataset, DEFAULT_PAGE_ROWS))
        return int(self.db.execute_values(sql, payload, page_size=page_size) or 0)

    # ------------------------------------------------------------------ 读
    def latest_ts(self, dataset: str) -> pd.Timestamp | None:
        if dataset not in DATASET_TABLE:
            raise ValueError(f"未知 P0 数据集: {dataset!r}")
        row = self.db.query(
            f'SELECT max(ts) FROM "{DATASET_TABLE[dataset]}"',
            [],
            fetch_all=False,
        )
        if not row or row[0] is None:
            return None
        return pd.Timestamp(row[0])

    def count(self, dataset: str) -> int:
        if dataset not in DATASET_TABLE:
            raise ValueError(f"未知 P0 数据集: {dataset!r}")
        row = self.db.query(
            f'SELECT count() FROM "{DATASET_TABLE[dataset]}"',
            [],
            fetch_all=False,
        )
        return int(row[0]) if row and row[0] is not None else 0

    def coverage(self, dataset: str) -> dict[str, Any]:
        """行数 / 代码数 / 起止日期，供回补后核验。"""
        if dataset not in DATASET_TABLE:
            raise ValueError(f"未知 P0 数据集: {dataset!r}")
        table = DATASET_TABLE[dataset]
        rows = self.db.query(
            f'SELECT count(), count_distinct(code), min(ts), max(ts) FROM "{table}"',
            [],
            fetch_all=False,
        )
        if not rows:
            return {"dataset": dataset, "table": table, "rows": 0}
        total, codes, first, last = rows
        return {
            "dataset": dataset,
            "table": table,
            "rows": int(total or 0),
            "codes": int(codes or 0),
            "first": str(first)[:10] if first is not None else None,
            "last": str(last)[:10] if last is not None else None,
        }

    def coverage_all(self) -> list[dict[str, Any]]:
        return [self.coverage(dataset) for dataset in P0_DATASETS]


__all__ = ["BasicRepository"]
