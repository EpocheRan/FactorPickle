"""P0 基础资料数据集的归一化：Tushare 原始记录 → QuestDB 行。

所有函数都是纯函数（输入原始记录、输出 dict 行），不访问网络/数据库，
方便离线测试。字段口径：

- 代码统一为 6 位（``600519.SH`` → ``600519``），只保留 A 股代码；
- ``YYYYMMDD`` / ``YYYY-MM-DD`` 统一成 ``datetime``；
- ``NaN/Inf/空串`` 统一成 ``None``（QuestDB DOUBLE 不接受 NaN）；
- 同一业务主键的重复记录按 ``update_flag``/``ann_date`` 取最新一条。
"""

from __future__ import annotations

import math
from datetime import date, datetime
from typing import Any, Callable, Iterable

import pandas as pd

from data_interface.common.security_code import (
    board_of,
    exchange_of,
    is_a_share_code,
    normalize_code,
)

#: Tushare ts_code 后缀 → 交易所（必须按后缀判定，000001 既是上证指数也是平安银行）
_SUFFIX_EXCHANGE = {"SH": "SSE", "SZ": "SZSE", "BJ": "BSE"}

SOURCE = "tushare"

#: 全市场名单里同一代码可能同时出现在 L/P/D 三份列表，按 L > P > D 取最可信状态
_STATUS_PRIORITY = {"L": 3, "P": 2, "D": 1}


def _code(value: Any) -> str | None:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    code = normalize_code(str(value))
    if len(code) != 6 or not is_a_share_code(code):
        return None
    return code


def _exchange(ts_code: Any, code: str) -> str:
    text = str(ts_code or "").strip().upper()
    if "." in text:
        exchange = _SUFFIX_EXCHANGE.get(text.rsplit(".", 1)[-1])
        if exchange:
            return exchange
    return exchange_of(code)


def _num(value: Any) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(result):
        return None
    return result


def _text(value: Any, limit: int = 200) -> str | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    if not text or text.lower() == "nan":
        return None
    return text[:limit]


def _date(value: Any) -> datetime | None:
    """``20240102`` / ``2024-01-02`` / ``datetime`` → ``datetime``。"""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    if not text:
        return None
    for fmt in ("%Y%m%d", "%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d"):
        try:
            return datetime.strptime(text[: len(fmt) + 2], fmt)
        except ValueError:
            continue
    parsed = pd.to_datetime(text, errors="coerce")
    if parsed is None or pd.isna(parsed):
        return None
    return parsed.to_pydatetime().replace(tzinfo=None)


def _is_st(name: str | None) -> bool:
    """``*ST`` / ``ST`` / ``S*ST`` 都算 ST；退市整理期的 ``退`` 不算。"""
    return bool(name) and "ST" in str(name).upper()


def _dedupe(
    rows: Iterable[dict[str, Any]],
    key: Callable[[dict[str, Any]], tuple],
    rank: Callable[[dict[str, Any]], int] | None = None,
) -> list[dict[str, Any]]:
    """按业务主键去重；``rank`` 更大者胜出（默认后者胜出）。"""
    best: dict[tuple, dict[str, Any]] = {}
    order: list[tuple] = []
    for row in rows:
        token = key(row)
        current = best.get(token)
        if current is None:
            order.append(token)
            best[token] = row
            continue
        if rank is None or rank(row) >= rank(current):
            best[token] = row
    return [best[token] for token in order]


# ---------------------------------------------------------------------- 主数据
def map_stock_basic(
    raw_rows: Iterable[dict[str, Any]],
    snapshot_ts: datetime,
    *,
    ingested_at: datetime | None = None,
    source: str = SOURCE,
) -> list[dict[str, Any]]:
    """全市场名单快照（L/P/D 合并），同一代码按 L > P > D 取一条。"""
    ingested = ingested_at or datetime.now()
    rows: list[dict[str, Any]] = []
    for item in raw_rows or []:
        code = _code(item.get("ts_code"))
        if not code:
            continue
        status = str(item.get("list_status") or "").strip().upper() or "L"
        name = _text(item.get("name"), 80)
        rows.append(
            {
                "ts": snapshot_ts,
                "code": code,
                "name": name,
                "fullname": _text(item.get("fullname")),
                "exchange": _exchange(item.get("ts_code"), code),
                "board": board_of(code, item.get("market")),
                "market": "A",
                "list_status": status,
                "list_date": _date(item.get("list_date")),
                "delist_date": _date(item.get("delist_date")),
                "industry": _text(item.get("industry"), 100),
                "area": _text(item.get("area"), 100),
                "is_hs": _text(item.get("is_hs"), 8),
                "act_name": _text(item.get("act_name"), 200),
                "act_ent_type": _text(item.get("act_ent_type"), 100),
                "is_st": _is_st(name),
                "source": source,
                "updated_at": ingested,
            }
        )
    return _dedupe(
        rows,
        key=lambda row: row["code"],
        rank=lambda row: _STATUS_PRIORITY.get(row["list_status"], 0),
    )


# ---------------------------------------------------------------------- 名称变更
def map_namechange(
    raw_rows: Iterable[dict[str, Any]],
    *,
    ingested_at: datetime | None = None,
    source: str = SOURCE,
) -> list[dict[str, Any]]:
    """历史名称（含 ST 区间）；``ts`` 取名称生效日 ``start_date``。"""
    ingested = ingested_at or datetime.now()
    rows: list[dict[str, Any]] = []
    for item in raw_rows or []:
        code = _code(item.get("ts_code"))
        if not code:
            continue
        effective = _date(item.get("start_date")) or _date(item.get("ann_date"))
        if effective is None:
            continue
        ann_date = _date(item.get("ann_date"))
        rows.append(
            {
                "ts": effective,
                "code": code,
                "name": _text(item.get("name"), 80),
                "end_date": _date(item.get("end_date")),
                "ann_date": ann_date,
                "change_reason": _text(item.get("change_reason")),
                "source": source,
                "ingested_at": ingested,
            }
        )
    return _dedupe(
        rows,
        key=lambda row: (row["ts"], row["code"]),
        rank=lambda row: int(row["ann_date"].timestamp()) if row["ann_date"] else 0,
    )


# ---------------------------------------------------------------------- 新股
def map_new_share(
    raw_rows: Iterable[dict[str, Any]],
    *,
    ingested_at: datetime | None = None,
    source: str = SOURCE,
) -> list[dict[str, Any]]:
    """新股发行/上市；``ts`` 取上市日 ``ipo_date``（缺失时退回收款日）。"""
    ingested = ingested_at or datetime.now()
    rows: list[dict[str, Any]] = []
    for item in raw_rows or []:
        code = _code(item.get("ts_code"))
        if not code:
            continue
        ipo_date = _date(item.get("ipo_date"))
        issue_date = _date(item.get("issue_date"))
        listed = ipo_date or issue_date
        if listed is None:
            continue
        rows.append(
            {
                "ts": listed,
                "code": code,
                "sub_code": _text(item.get("sub_code"), 16),
                "name": _text(item.get("name"), 80),
                "ipo_date": ipo_date,
                "issue_date": issue_date,
                "amount": _num(item.get("amount")),
                "market_amount": _num(item.get("market_amount")),
                "price": _num(item.get("price")),
                "pe": _num(item.get("pe")),
                "limit_amount": _num(item.get("limit_amount")),
                "funds": _num(item.get("funds")),
                "ballot": _num(item.get("ballot")),
                "source": source,
                "ingested_at": ingested,
            }
        )
    return _dedupe(rows, key=lambda row: (row["ts"], row["code"]))


# ---------------------------------------------------------------------- 停复牌
def map_suspend_d(
    raw_rows: Iterable[dict[str, Any]],
    *,
    ingested_at: datetime | None = None,
    source: str = SOURCE,
) -> list[dict[str, Any]]:
    """停复牌明细：同一交易日同一代码可同时有 ``S``（停牌）与 ``R``（复牌）两条。"""
    ingested = ingested_at or datetime.now()
    rows: list[dict[str, Any]] = []
    for item in raw_rows or []:
        code = _code(item.get("ts_code"))
        day = _date(item.get("trade_date"))
        if not code or day is None:
            continue
        suspend_type = str(item.get("suspend_type") or "").strip().upper() or None
        rows.append(
            {
                "ts": day,
                "code": code,
                "suspend_timing": _text(item.get("suspend_timing"), 32),
                "suspend_type": suspend_type,
                "source": source,
                "ingested_at": ingested,
            }
        )
    return _dedupe(rows, key=lambda row: (row["ts"], row["code"], row["suspend_type"]))


# ---------------------------------------------------------------------- 涨跌停
def map_stk_limit(
    raw_rows: Iterable[dict[str, Any]],
    *,
    ingested_at: datetime | None = None,
    source: str = SOURCE,
) -> list[dict[str, Any]]:
    """每日涨跌停价格。"""
    ingested = ingested_at or datetime.now()
    rows: list[dict[str, Any]] = []
    for item in raw_rows or []:
        code = _code(item.get("ts_code"))
        day = _date(item.get("trade_date"))
        if not code or day is None:
            continue
        rows.append(
            {
                "ts": day,
                "code": code,
                "up_limit": _num(item.get("up_limit")),
                "down_limit": _num(item.get("down_limit")),
                "source": source,
                "ingested_at": ingested,
            }
        )
    return _dedupe(rows, key=lambda row: (row["ts"], row["code"]))


# ---------------------------------------------------------------------- 披露计划
def map_disclosure_date(
    raw_rows: Iterable[dict[str, Any]],
    *,
    ingested_at: datetime | None = None,
    source: str = SOURCE,
) -> list[dict[str, Any]]:
    """财报披露计划；``ts`` 取报告期 ``end_date``，保留预约/实际披露日。"""
    ingested = ingested_at or datetime.now()
    rows: list[dict[str, Any]] = []
    for item in raw_rows or []:
        code = _code(item.get("ts_code"))
        period_end = _date(item.get("end_date"))
        if not code or period_end is None:
            continue
        rows.append(
            {
                "ts": period_end,
                "code": code,
                "ann_date": _date(item.get("ann_date")),
                "pre_date": _date(item.get("pre_date")),
                "actual_date": _date(item.get("actual_date")),
                "source": source,
                "ingested_at": ingested,
            }
        )
    return _dedupe(
        rows,
        key=lambda row: (row["ts"], row["code"]),
        rank=lambda row: int(row["ann_date"].timestamp()) if row["ann_date"] else 0,
    )


# ---------------------------------------------------------------------- 业绩预告
def map_forecast(
    raw_rows: Iterable[dict[str, Any]],
    *,
    ingested_at: datetime | None = None,
    source: str = SOURCE,
) -> list[dict[str, Any]]:
    """业绩预告；``ts`` 取公告日 ``ann_date``，同一公告的修订版按 update_flag 取最新。"""
    ingested = ingested_at or datetime.now()
    rows: list[dict[str, Any]] = []
    for item in raw_rows or []:
        code = _code(item.get("ts_code"))
        day = _date(item.get("ann_date"))
        if not code or day is None:
            continue
        rows.append(
            {
                "ts": day,
                "code": code,
                "end_date": _date(item.get("end_date")),
                "report_type": _text(item.get("type"), 32),
                "p_change_min": _num(item.get("p_change_min")),
                "p_change_max": _num(item.get("p_change_max")),
                "net_profit_min": _num(item.get("net_profit_min")),
                "net_profit_max": _num(item.get("net_profit_max")),
                "last_parent_net": _num(item.get("last_parent_net")),
                "first_ann_date": _date(item.get("first_ann_date")),
                "summary": _text(item.get("summary")),
                "change_reason": _text(item.get("change_reason"), 2000),
                "update_flag": _text(item.get("update_flag"), 8),
                "source": source,
                "ingested_at": ingested,
            }
        )
    return _dedupe(
        rows,
        key=lambda row: (
            row["ts"],
            row["code"],
            row["end_date"],
            row["report_type"],
        ),
        rank=lambda row: _flag(row["update_flag"]),
    )


# ---------------------------------------------------------------------- 业绩快报
def map_express(
    raw_rows: Iterable[dict[str, Any]],
    *,
    ingested_at: datetime | None = None,
    source: str = SOURCE,
) -> list[dict[str, Any]]:
    """业绩快报；``ts`` 取公告日，修订版按 update_flag 取最新。"""
    ingested = ingested_at or datetime.now()
    rows: list[dict[str, Any]] = []
    for item in raw_rows or []:
        code = _code(item.get("ts_code"))
        day = _date(item.get("ann_date"))
        if not code or day is None:
            continue
        rows.append(
            {
                "ts": day,
                "code": code,
                "end_date": _date(item.get("end_date")),
                "revenue": _num(item.get("revenue")),
                "operate_profit": _num(item.get("operate_profit")),
                "total_profit": _num(item.get("total_profit")),
                "n_income": _num(item.get("n_income")),
                "total_assets": _num(item.get("total_assets")),
                "total_hldr_eqy_exc_min_int": _num(
                    item.get("total_hldr_eqy_exc_min_int")
                ),
                "diluted_eps": _num(item.get("diluted_eps")),
                "diluted_roe": _num(item.get("diluted_roe")),
                "yoy_net_profit": _num(item.get("yoy_net_profit")),
                "bps": _num(item.get("bps")),
                "perf_summary": _text(item.get("perf_summary")),
                "update_flag": _text(item.get("update_flag"), 8),
                "source": source,
                "ingested_at": ingested,
            }
        )
    return _dedupe(
        rows,
        key=lambda row: (row["ts"], row["code"], row["end_date"]),
        rank=lambda row: _flag(row["update_flag"]),
    )


def _flag(value: Any) -> int:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return 0


#: 数据集 → 映射函数（(raw_rows, ingested_at) 之外无额外参数）
MAPPERS: dict[str, Callable[..., list[dict[str, Any]]]] = {
    "namechange": map_namechange,
    "new_share": map_new_share,
    "suspend_d": map_suspend_d,
    "stk_limit": map_stk_limit,
    "disclosure_date": map_disclosure_date,
    "forecast": map_forecast,
    "express": map_express,
}

__all__ = [
    "MAPPERS",
    "map_disclosure_date",
    "map_express",
    "map_forecast",
    "map_namechange",
    "map_new_share",
    "map_stk_limit",
    "map_stock_basic",
    "map_suspend_d",
]
