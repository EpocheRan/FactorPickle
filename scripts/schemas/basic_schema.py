"""P0 基础资料表结构（QuestDB）。

设计要点
--------
- 八张表都按各自业务日期做 designated timestamp（``ts``），按年分区；
  只有主数据快照 ``stock_basic_history`` 按月分区（同 ``stock_basic_info``）。
- 全部 ``WAL + DEDUP UPSERT KEYS``，主键包含 ``ts``；重复回补幂等覆盖。
- ``namechange`` / ``new_share`` / ``suspend_d`` / ``stk_limit`` 是**事实表**，
  主键为 ``(ts, code[, suspend_type])``；``disclosure_date`` / ``forecast`` /
  ``express`` 保留公告日或报告期，便于 PIT 对齐。
- ``stock_basic_history`` 是**全市场快照**（L/P/D 均在），不覆盖当前画像表
  ``stock_basic_info``；历史名称/ST 区间由 ``namechange`` 提供。
- ``market_suspension`` 与 ``fin_disclosure_calendar`` 保持原有语义：
  前者是 screener 的停牌/无数据负缓存，后者是预约披露日快照；
  P0 的逐日停复牌与披露计划分别落在 ``suspend_d`` / ``disclosure_date``。
"""

from __future__ import annotations

import logging

from data_interface.basic.models import (
    DISCLOSURE_DATE_COLUMNS,
    EXPRESS_COLUMNS,
    FORECAST_COLUMNS,
    NAMECHANGE_COLUMNS,
    NEW_SHARE_COLUMNS,
    STK_LIMIT_COLUMNS,
    STOCK_BASIC_HISTORY_COLUMNS,
    SUSPEND_D_COLUMNS,
)

logger = logging.getLogger("Scripts.BasicSchema")


def _ddl() -> list[str]:
    return [
        """
        CREATE TABLE IF NOT EXISTS "stock_basic_history" (
            ts TIMESTAMP,
            code SYMBOL,
            name STRING,
            fullname STRING,
            exchange SYMBOL,
            board SYMBOL,
            market SYMBOL,
            list_status SYMBOL,
            list_date TIMESTAMP,
            delist_date TIMESTAMP,
            industry STRING,
            area STRING,
            is_hs SYMBOL,
            act_name STRING,
            act_ent_type STRING,
            is_st BOOLEAN,
            source SYMBOL,
            updated_at TIMESTAMP
        ) TIMESTAMP(ts) PARTITION BY MONTH WAL
        DEDUP UPSERT KEYS(ts, code)
        """,
        """
        CREATE TABLE IF NOT EXISTS "namechange" (
            ts TIMESTAMP,
            code SYMBOL,
            name STRING,
            end_date TIMESTAMP,
            ann_date TIMESTAMP,
            change_reason STRING,
            source SYMBOL,
            ingested_at TIMESTAMP
        ) TIMESTAMP(ts) PARTITION BY YEAR WAL
        DEDUP UPSERT KEYS(ts, code)
        """,
        """
        CREATE TABLE IF NOT EXISTS "new_share" (
            ts TIMESTAMP,
            code SYMBOL,
            sub_code SYMBOL,
            name STRING,
            ipo_date TIMESTAMP,
            issue_date TIMESTAMP,
            amount DOUBLE,
            market_amount DOUBLE,
            price DOUBLE,
            pe DOUBLE,
            limit_amount DOUBLE,
            funds DOUBLE,
            ballot DOUBLE,
            source SYMBOL,
            ingested_at TIMESTAMP
        ) TIMESTAMP(ts) PARTITION BY YEAR WAL
        DEDUP UPSERT KEYS(ts, code)
        """,
        """
        CREATE TABLE IF NOT EXISTS "suspend_d" (
            ts TIMESTAMP,
            code SYMBOL,
            suspend_timing STRING,
            suspend_type SYMBOL,
            source SYMBOL,
            ingested_at TIMESTAMP
        ) TIMESTAMP(ts) PARTITION BY YEAR WAL
        DEDUP UPSERT KEYS(ts, code, suspend_type)
        """,
        """
        CREATE TABLE IF NOT EXISTS "stk_limit" (
            ts TIMESTAMP,
            code SYMBOL,
            up_limit DOUBLE,
            down_limit DOUBLE,
            source SYMBOL,
            ingested_at TIMESTAMP
        ) TIMESTAMP(ts) PARTITION BY YEAR WAL
        DEDUP UPSERT KEYS(ts, code)
        """,
        """
        CREATE TABLE IF NOT EXISTS "disclosure_date" (
            ts TIMESTAMP,
            code SYMBOL,
            ann_date TIMESTAMP,
            pre_date TIMESTAMP,
            actual_date TIMESTAMP,
            source SYMBOL,
            ingested_at TIMESTAMP
        ) TIMESTAMP(ts) PARTITION BY YEAR WAL
        DEDUP UPSERT KEYS(ts, code)
        """,
        """
        CREATE TABLE IF NOT EXISTS "forecast" (
            ts TIMESTAMP,
            code SYMBOL,
            end_date TIMESTAMP,
            report_type SYMBOL,
            p_change_min DOUBLE,
            p_change_max DOUBLE,
            net_profit_min DOUBLE,
            net_profit_max DOUBLE,
            last_parent_net DOUBLE,
            first_ann_date TIMESTAMP,
            summary STRING,
            change_reason STRING,
            update_flag SYMBOL,
            source SYMBOL,
            ingested_at TIMESTAMP
        ) TIMESTAMP(ts) PARTITION BY YEAR WAL
        DEDUP UPSERT KEYS(ts, code, end_date, report_type)
        """,
        """
        CREATE TABLE IF NOT EXISTS "express" (
            ts TIMESTAMP,
            code SYMBOL,
            end_date TIMESTAMP,
            revenue DOUBLE,
            operate_profit DOUBLE,
            total_profit DOUBLE,
            n_income DOUBLE,
            total_assets DOUBLE,
            total_hldr_eqy_exc_min_int DOUBLE,
            diluted_eps DOUBLE,
            diluted_roe DOUBLE,
            yoy_net_profit DOUBLE,
            bps DOUBLE,
            perf_summary STRING,
            update_flag SYMBOL,
            source SYMBOL,
            ingested_at TIMESTAMP
        ) TIMESTAMP(ts) PARTITION BY YEAR WAL
        DEDUP UPSERT KEYS(ts, code, end_date)
        """,
    ]


#: 表名 → 写入列（回补核验与测试用）
TABLE_COLUMNS: dict[str, list[str]] = {
    "stock_basic_history": STOCK_BASIC_HISTORY_COLUMNS,
    "namechange": NAMECHANGE_COLUMNS,
    "new_share": NEW_SHARE_COLUMNS,
    "suspend_d": SUSPEND_D_COLUMNS,
    "stk_limit": STK_LIMIT_COLUMNS,
    "disclosure_date": DISCLOSURE_DATE_COLUMNS,
    "forecast": FORECAST_COLUMNS,
    "express": EXPRESS_COLUMNS,
}


def create_basic_tables(db, *, dry_run: bool = False) -> list[str]:
    statements = _ddl()
    for sql in statements:
        if dry_run:
            logger.info("[dry-run] %s", sql.strip().splitlines()[0])
        else:
            db.execute_ddl(sql)
    return statements
