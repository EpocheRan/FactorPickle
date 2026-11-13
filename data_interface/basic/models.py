"""P0 基础资料/公司行动数据集的表与列定义。

这些数据集是「可选池、停复牌、涨跌停、披露与业绩预告」的最小集合，
由 ``acquisition`` 的 ``p0`` 数据集组按交易日/报告期回补，写入同名 QuestDB 表。

约定：

- 每张表都用业务日期做 designated timestamp（``ts``），按年分区；
- 可重复同步的表全部 ``WAL + DEDUP UPSERT KEYS``，主键包含 ``ts``；
- ``ingested_at`` 记录写入时刻，只用于审计，不参与主键。
"""

from __future__ import annotations

from typing import Final

#: 数据集 → QuestDB 表名（两者目前一致，保留映射便于以后改表名）
DATASET_TABLE: Final[dict[str, str]] = {
    "stock_basic_history": "stock_basic_history",
    "namechange": "namechange",
    "new_share": "new_share",
    "suspend_d": "suspend_d",
    "stk_limit": "stk_limit",
    "disclosure_date": "disclosure_date",
    "forecast": "forecast",
    "express": "express",
}

#: P0 数据集执行顺序：先主数据，再按日期/报告期批量
P0_DATASETS: Final[tuple[str, ...]] = (
    "stock_basic_history",
    "namechange",
    "new_share",
    "suspend_d",
    "stk_limit",
    "disclosure_date",
    "forecast",
    "express",
)

#: 取数粒度：snapshot=每次运行一个全市场快照；days_* = 按日；
#: periods = 按报告期；range = 按日期区间分年拉取
DATASET_MODE: Final[dict[str, str]] = {
    "stock_basic_history": "snapshot",
    "namechange": "range",
    "new_share": "range",
    "suspend_d": "days_trading",
    "stk_limit": "days_trading",
    "disclosure_date": "periods",
    "forecast": "days_calendar",
    "express": "periods",
}

STOCK_BASIC_HISTORY_COLUMNS: Final[list[str]] = [
    "ts",
    "code",
    "name",
    "fullname",
    "exchange",
    "board",
    "market",
    "list_status",
    "list_date",
    "delist_date",
    "industry",
    "area",
    "is_hs",
    "act_name",
    "act_ent_type",
    "is_st",
    "source",
    "updated_at",
]

NAMECHANGE_COLUMNS: Final[list[str]] = [
    "ts",
    "code",
    "name",
    "end_date",
    "ann_date",
    "change_reason",
    "source",
    "ingested_at",
]

NEW_SHARE_COLUMNS: Final[list[str]] = [
    "ts",
    "code",
    "sub_code",
    "name",
    "ipo_date",
    "issue_date",
    "amount",
    "market_amount",
    "price",
    "pe",
    "limit_amount",
    "funds",
    "ballot",
    "source",
    "ingested_at",
]

SUSPEND_D_COLUMNS: Final[list[str]] = [
    "ts",
    "code",
    "suspend_timing",
    "suspend_type",
    "source",
    "ingested_at",
]

STK_LIMIT_COLUMNS: Final[list[str]] = [
    "ts",
    "code",
    "up_limit",
    "down_limit",
    "source",
    "ingested_at",
]

DISCLOSURE_DATE_COLUMNS: Final[list[str]] = [
    "ts",
    "code",
    "ann_date",
    "pre_date",
    "actual_date",
    "source",
    "ingested_at",
]

FORECAST_COLUMNS: Final[list[str]] = [
    "ts",
    "code",
    "end_date",
    "report_type",
    "p_change_min",
    "p_change_max",
    "net_profit_min",
    "net_profit_max",
    "last_parent_net",
    "first_ann_date",
    "summary",
    "change_reason",
    "update_flag",
    "source",
    "ingested_at",
]

EXPRESS_COLUMNS: Final[list[str]] = [
    "ts",
    "code",
    "end_date",
    "revenue",
    "operate_profit",
    "total_profit",
    "n_income",
    "total_assets",
    "total_hldr_eqy_exc_min_int",
    "diluted_eps",
    "diluted_roe",
    "yoy_net_profit",
    "bps",
    "perf_summary",
    "update_flag",
    "source",
    "ingested_at",
]

#: 数据集 → 写入列（顺序与 DDL 一致）
DATASET_COLUMNS: Final[dict[str, list[str]]] = {
    "stock_basic_history": STOCK_BASIC_HISTORY_COLUMNS,
    "namechange": NAMECHANGE_COLUMNS,
    "new_share": NEW_SHARE_COLUMNS,
    "suspend_d": SUSPEND_D_COLUMNS,
    "stk_limit": STK_LIMIT_COLUMNS,
    "disclosure_date": DISCLOSURE_DATE_COLUMNS,
    "forecast": FORECAST_COLUMNS,
    "express": EXPRESS_COLUMNS,
}

#: 单次 INSERT 的行数。``forecast`` 的 ``summary`` / ``change_reason`` 单行可达
#: 数 KB，1000 行会让 PG wire 报文过大、QuestDB 直接断开连接，因此单独调小。
DATASET_PAGE_ROWS: Final[dict[str, int]] = {
    "forecast": 200,
}

DEFAULT_PAGE_ROWS: Final[int] = 1000

__all__ = [
    "DATASET_COLUMNS",
    "DATASET_MODE",
    "DATASET_PAGE_ROWS",
    "DATASET_TABLE",
    "DEFAULT_PAGE_ROWS",
    "DISCLOSURE_DATE_COLUMNS",
    "EXPRESS_COLUMNS",
    "FORECAST_COLUMNS",
    "NAMECHANGE_COLUMNS",
    "NEW_SHARE_COLUMNS",
    "P0_DATASETS",
    "STK_LIMIT_COLUMNS",
    "STOCK_BASIC_HISTORY_COLUMNS",
    "SUSPEND_D_COLUMNS",
]
