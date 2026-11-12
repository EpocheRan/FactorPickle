"""P1 event-axis features for the Alpha Runner.

Raw event tables (``block_trade`` / ``top_list`` / ``repurchase`` /
``stk_holdernumber``) are sparse and event-only.  This module turns them into
daily date x code matrices that the existing PanelBuilder can expose as factor
fields.

Point-in-time contract
----------------------
Every raw table's designated ``ts`` is the **announcement / disclosure date**
(verified in ``acquisition.backfill.phases``: repurchase, share_float and
stk_holdernumber are written with ``ann_date``; top_list / block_trade are
trade-day disclosures).  Features are lagged by one trading day before being
returned, so a value at date D only uses information disclosed on or before the
previous trading day.  The Runner/Evaluator can therefore treat them as normal
daily fields without a separate T+1 shift.

Event-flow features are zero-filled on days with no event (true zero), while
holder-number state is forward-filled from the latest announcement.  This makes
the returned matrices valid for coverage accounting: raw event sparsity is not
a missing-data gap.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Iterable

import numpy as np
import pandas as pd

from data_interface.common.questdb_client import QuestDBClient, sql_literal
from data_interface.common.trading_calendar import TradingCalendar


P1_EVENT_FIELDS: dict[str, list[str]] = {
    "block_trade": ["block_amount", "block_vol", "block_vwap", "block_count"],
    "top_list": ["top_amount", "top_l_buy", "top_l_sell", "top_net_amount", "top_count"],
    "repurchase": ["rep_amount", "rep_vol", "rep_count"],
    "stk_holdernumber": ["holder_num", "holder_chg", "holder_days_since"],
}
ALL_P1_FIELDS = sorted({field for fields in P1_EVENT_FIELDS.values() for field in fields})


@dataclass
class P1EventBundle:
    frames: dict[str, pd.DataFrame] = field(default_factory=dict)
    raw_rows: dict[str, int] = field(default_factory=dict)
    dates: pd.DatetimeIndex | None = None
    codes: list[str] = field(default_factory=list)

    @property
    def fields(self) -> list[str]:
        return sorted({name for name in self.frames})


def _as_date(value: Any) -> pd.Timestamp:
    return pd.Timestamp(value).normalize()


def _trading_days(start: str, end: str) -> pd.DatetimeIndex:
    calendar = TradingCalendar()
    try:
        days = calendar.trading_days_or_weekdays(start, end)
    except Exception:  # noqa: BLE001 - fallback is deliberately conservative
        days = pd.date_range(start, end, freq="B")
    dates = pd.DatetimeIndex(pd.to_datetime([str(day)[:10] for day in days])).normalize()
    return dates[~dates.duplicated()].sort_values()


def _codes_sql(codes: Iterable[str]) -> str:
    values = sorted({str(code) for code in codes if str(code).strip()})
    if not values:
        raise ValueError("P1 event features require at least one code")
    return ",".join(sql_literal(code) for code in values)


def _query(db: Any, table: str, columns: list[str], codes: list[str], start: str, end: str) -> pd.DataFrame:
    sql = (
        f"select ts, code, {', '.join(columns)} from {table} "
        f"where code in ({_codes_sql(codes)}) "
        f"and ts between {sql_literal(start)} and {sql_literal(end)}"
    )
    frame = db.query_dataframe(sql)
    if frame is None or frame.empty:
        return pd.DataFrame(columns=["ts", "code"] + columns)
    if isinstance(frame.index, pd.DatetimeIndex) or frame.index.name == "ts":
        frame = frame.reset_index()
    if "ts" not in frame.columns:
        raise ValueError(f"{table} query did not return ts")
    frame["ts"] = pd.to_datetime(frame["ts"], errors="coerce").dt.tz_localize(None).dt.normalize()
    frame = frame.dropna(subset=["ts"])
    frame["code"] = frame["code"].astype(str)
    for column in columns:
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame


def _flow_frame(
    raw: pd.DataFrame,
    *,
    dates: pd.DatetimeIndex,
    codes: list[str],
    aggregations: dict[str, tuple[str, str]],
    trim_start: pd.Timestamp,
    trim_end: pd.Timestamp,
) -> dict[str, pd.DataFrame]:
    """Aggregate raw event rows to one date x code matrix per field, zero-fill, then lag one day."""
    result: dict[str, pd.DataFrame] = {}
    for name in aggregations:
        if raw.empty:
            result[name] = pd.DataFrame(0.0, index=dates, columns=codes).loc[trim_start:trim_end].astype(float)
    if raw.empty:
        return result
    grouped = raw.groupby(["ts", "code"], dropna=False).agg(
        **{name: pd.NamedAgg(column=source, aggfunc=aggfunc) for name, (source, aggfunc) in aggregations.items()}
    )
    for name in aggregations:
        if name not in grouped.columns:
            continue
        frame = grouped[name].unstack("code").reindex(index=dates, columns=codes)
        result[name] = frame.fillna(0.0).shift(1).fillna(0.0).loc[trim_start:trim_end].astype(float)
    return result

def _holder_frame(
    raw: pd.DataFrame,
    *,
    dates: pd.DatetimeIndex,
    codes: list[str],
    trim_start: pd.Timestamp,
    trim_end: pd.Timestamp,
) -> dict[str, pd.DataFrame]:
    if raw.empty:
        empty = pd.DataFrame(np.nan, index=dates, columns=codes)
        return {
            "holder_num": empty.loc[trim_start:trim_end],
            "holder_chg": empty.loc[trim_start:trim_end],
            "holder_days_since": empty.loc[trim_start:trim_end],
        }
    event = (
        raw.dropna(subset=["holder_num"])
        .groupby(["ts", "code"], dropna=False)
        .agg(holder_num=("holder_num", "last"))
        .reset_index()
        .pivot(index="ts", columns="code", values="holder_num")
        .reindex(columns=codes)
    )
    if event.empty:
        empty = pd.DataFrame(np.nan, index=dates, columns=codes)
        return {"holder_num": empty.loc[trim_start:trim_end], "holder_chg": empty.loc[trim_start:trim_end], "holder_days_since": empty.loc[trim_start:trim_end]}
    # Change is computed between consecutive *announced* values, not between
    # adjacent calendar rows (rows between announcements are missing).
    change = event.ffill().pct_change(fill_method=None).replace([np.inf, -np.inf], np.nan).where(event.notna())
    state = event.reindex(index=dates).ffill()
    change_daily = change.reindex(index=dates).ffill()
    event_mask = event.notna().to_numpy()
    event_date_matrix = np.where(
        event_mask,
        np.repeat(event.index.values[:, None], event.shape[1], axis=1),
        np.datetime64("NaT"),
    )
    last_event = pd.DataFrame(
        event_date_matrix,
        index=event.index,
        columns=event.columns,
    ).reindex(dates).ffill()
    # Availability: on date D only announcements made before D are usable, so
    # both the state and the "days since" reference are shifted by one trading day.
    last_event_available = last_event.shift(1)
    days_since = (dates.values[:, None] - last_event_available.to_numpy()) / np.timedelta64(1, "D")
    holder_days = pd.DataFrame(days_since, index=dates, columns=codes)
    holder_num = state.shift(1).loc[trim_start:trim_end]
    holder_chg = change_daily.shift(1).loc[trim_start:trim_end]
    holder_days = holder_days.loc[trim_start:trim_end]
    return {"holder_num": holder_num, "holder_chg": holder_chg, "holder_days_since": holder_days}

def load_p1_events(
    codes: list[str],
    start: str,
    end: str,
    *,
    db: Any | None = None,
) -> P1EventBundle:
    """Load and aggregate all P1 event axes over ``[start, end]``.

    The event-flow matrices are zero-filled and PIT-lagged; holder state is
    forward-filled and PIT-lagged.  Dates are trading days from the shared
    TradingCalendar, so PanelBuilder can align them to the market close index.
    """
    codes = sorted({str(code) for code in codes if str(code).strip()})
    if not codes:
        raise ValueError("P1 event features require at least one code")
    db = db or QuestDBClient()
    trim_start = _as_date(start)
    trim_end = _as_date(end)
    load_start = (trim_start - timedelta(days=45)).date().isoformat()
    holder_start = (trim_start - timedelta(days=730)).date().isoformat()
    dates_all = _trading_days(load_start, end)
    dates = dates_all[(dates_all >= trim_start) & (dates_all <= trim_end)]
    if dates.empty:
        raise ValueError(f"no trading days in P1 window {start}..{end}")

    bundle = P1EventBundle(dates=dates, codes=codes)
    block = _query(db, "block_trade", ["price", "vol", "amount"], codes, load_start, end)
    top = _query(db, "top_list", ["amount", "l_buy", "l_sell", "net_amount"], codes, load_start, end)
    rep = _query(db, "repurchase", ["vol", "amount"], codes, load_start, end)
    holder = _query(db, "stk_holdernumber", ["holder_num"], codes, holder_start, end)
    bundle.raw_rows = {
        "block_trade": int(len(block)),
        "top_list": int(len(top)),
        "repurchase": int(len(rep)),
        "stk_holdernumber": int(len(holder)),
    }

    flow = _flow_frame(
        block,
        dates=dates_all,
        codes=codes,
        aggregations={
            "block_amount": ("amount", "sum"),
            "block_vol": ("vol", "sum"),
            "block_count": ("amount", "count"),
        },
        trim_start=trim_start,
        trim_end=trim_end,
    )
    flow["block_vwap"] = flow["block_amount"] / flow["block_vol"].replace(0.0, np.nan)
    flow["block_vwap"] = flow["block_vwap"].replace([np.inf, -np.inf], np.nan).fillna(0.0)
    bundle.frames.update({name: flow[name] for name in P1_EVENT_FIELDS["block_trade"]})

    top_frame = _flow_frame(
        top,
        dates=dates_all,
        codes=codes,
        aggregations={
            "top_amount": ("amount", "sum"),
            "top_l_buy": ("l_buy", "sum"),
            "top_l_sell": ("l_sell", "sum"),
            "top_net_amount": ("net_amount", "sum"),
            "top_count": ("amount", "count"),
        },
        trim_start=trim_start,
        trim_end=trim_end,
    )
    bundle.frames.update({name: top_frame[name] for name in P1_EVENT_FIELDS["top_list"]})

    rep_frame = _flow_frame(
        rep,
        dates=dates_all,
        codes=codes,
        aggregations={
            "rep_amount": ("amount", "sum"),
            "rep_vol": ("vol", "sum"),
            "rep_count": ("amount", "count"),
        },
        trim_start=trim_start,
        trim_end=trim_end,
    )
    bundle.frames.update({name: rep_frame[name] for name in P1_EVENT_FIELDS["repurchase"]})

    holder_frame = _holder_frame(holder, dates=dates_all, codes=codes, trim_start=trim_start, trim_end=trim_end)
    bundle.frames.update({name: holder_frame[name] for name in P1_EVENT_FIELDS["stk_holdernumber"]})
    return bundle
