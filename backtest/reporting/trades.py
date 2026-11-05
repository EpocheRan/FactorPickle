"""回合交易：把逐笔 fills 按 FIFO 配成 round trip。"""

from __future__ import annotations

from collections import defaultdict, deque

import pandas as pd

ROUND_TRIP_COLUMNS = [
    "symbol",
    "entry_date",
    "exit_date",
    "entry_price",
    "exit_price",
    "shares",
    "pnl",
    "ret",
    "days",
    "buy_fees",
    "sell_fees",
    "reason",
]


def build_round_trips(orders: pd.DataFrame | None) -> pd.DataFrame:
    if orders is None or orders.empty:
        return pd.DataFrame(columns=ROUND_TRIP_COLUMNS)

    frame = orders.copy()
    required = {"type", "symbol", "date", "shares", "fill_price"}
    if not required.issubset(frame.columns):
        return pd.DataFrame(columns=ROUND_TRIP_COLUMNS)
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame = frame.dropna(subset=["date", "symbol"])
    if frame.empty:
        return pd.DataFrame(columns=ROUND_TRIP_COLUMNS)
    frame["shares"] = pd.to_numeric(frame["shares"], errors="coerce").fillna(0.0).abs()
    frame["fill_price"] = pd.to_numeric(frame["fill_price"], errors="coerce").fillna(0.0)
    frame["fees"] = pd.to_numeric(frame.get("fees", 0.0), errors="coerce").fillna(0.0)
    frame["type"] = frame["type"].astype(str).str.lower()
    frame = frame.sort_values(["date", "symbol"])

    lots: dict[str, deque[dict]] = defaultdict(deque)
    trips: list[dict] = []
    for row in frame.itertuples(index=False):
        symbol = str(row.symbol)
        shares = float(row.shares)
        if shares <= 0:
            continue
        price = float(row.fill_price)
        fees = float(row.fees)
        side = str(row.type)
        if side == "buy":
            lots[symbol].append(
                {
                    "date": row.date,
                    "price": price,
                    "shares": shares,
                    "fees": fees,
                }
            )
            continue
        if side != "sell":
            continue
        remaining = shares
        while remaining > 1e-9 and lots[symbol]:
            lot = lots[symbol][0]
            take = min(remaining, float(lot["shares"]))
            buy_fee = float(lot["fees"]) * take / float(lot["shares"]) if lot["shares"] else 0.0
            sell_fee = fees * take / shares if shares else 0.0
            pnl = (price - float(lot["price"])) * take - buy_fee - sell_fee
            cost = float(lot["price"]) * take
            trips.append(
                {
                    "symbol": symbol,
                    "entry_date": pd.Timestamp(lot["date"]),
                    "exit_date": pd.Timestamp(row.date),
                    "entry_price": float(lot["price"]),
                    "exit_price": price,
                    "shares": take,
                    "pnl": pnl,
                    "ret": pnl / cost if cost > 0 else 0.0,
                    "days": (pd.Timestamp(row.date) - pd.Timestamp(lot["date"])).days,
                    "buy_fees": buy_fee,
                    "sell_fees": sell_fee,
                    "reason": str(getattr(row, "reason", "") or "unknown"),
                }
            )
            lot["shares"] = float(lot["shares"]) - take
            lot["fees"] = float(lot["fees"]) - buy_fee
            remaining -= take
            if lot["shares"] <= 1e-9:
                lots[symbol].popleft()
    if not trips:
        return pd.DataFrame(columns=ROUND_TRIP_COLUMNS)
    return pd.DataFrame(trips, columns=ROUND_TRIP_COLUMNS).sort_values("exit_date").reset_index(drop=True)
