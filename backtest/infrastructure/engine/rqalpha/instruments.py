"""FinBridge 代码 → RQAlpha Instrument。"""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Iterable, Mapping

from rqalpha.const import MARKET
from rqalpha.model.instrument import Instrument

from data_interface.common.security_code import board_of, exchange_of, normalize_code

_EXCHANGE = {"SSE": "XSHG", "SZSE": "XSHE", "BSE": "BJSE"}
_BOARD = {"STAR": "KSH", "GEM": "GEM", "MAIN": "MainBoard", "BSE": "MainBoard", "B_SHARE": "MainBoard"}


def to_order_book_id(code: str) -> str:
    normalized = normalize_code(code)
    return f"{normalized}.{_EXCHANGE[exchange_of(normalized)]}"


def code_of(order_book_id: str) -> str:
    return normalize_code(order_book_id)


def _as_datetime(value: date | datetime | None, default: datetime) -> datetime:
    if value is None:
        return default
    if isinstance(value, datetime):
        return value
    return datetime.combine(value, time())


def build_instrument(
    code: str,
    *,
    listed_date: date | datetime | None = None,
    delist_date: date | datetime | None = None,
    is_st: bool = False,
    lot_size: int = 100,
) -> Instrument:
    normalized = normalize_code(code)
    exchange = exchange_of(normalized)
    board = board_of(normalized)
    payload = {
        "order_book_id": f"{normalized}.{_EXCHANGE[exchange]}",
        "symbol": normalized,
        "type": "CS",
        "exchange": _EXCHANGE[exchange],
        "board_type": _BOARD.get(board, "MainBoard"),
        # 科创板 200 股起、1 股递增；RQAlpha 用 round_lot=1 表达，最小 200 由下单层保证。
        "round_lot": 1 if board == "STAR" else int(lot_size),
        "listed_date": _as_datetime(listed_date, datetime(1990, 1, 1)),
        "de_listed_date": _as_datetime(delist_date, datetime(2999, 12, 31)),
        "market_tplus": 1,
        "status": "Active",
        "special_type": "ST" if is_st else "Normal",
        "trading_hours": "09:30-11:30,13:00-15:00",
    }
    return Instrument(payload, market=MARKET.CN)


def build_instruments(
    codes: Iterable[str],
    security_status=None,
    *,
    lot_size: int = 100,
) -> list[Instrument]:
    instruments: list[Instrument] = []
    for code in sorted({normalize_code(item) for item in codes if normalize_code(item)}):
        listed = None
        delisted = None
        is_st = False
        if security_status is not None:
            listed = getattr(security_status, "list_dates", {}).get(code)
            delisted = getattr(security_status, "delist_dates", {}).get(code)
            is_st = bool(getattr(security_status, "current_st", {}).get(code, False))
        instruments.append(
            build_instrument(
                code,
                listed_date=listed,
                delist_date=delisted,
                is_st=is_st,
                lot_size=lot_size,
            )
        )
    return instruments


def instrument_map(instruments: Iterable[Instrument]) -> Mapping[str, Instrument]:
    return {item.order_book_id: item for item in instruments}
