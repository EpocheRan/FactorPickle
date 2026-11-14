"""PIT state features from event tables (earnings forecast/express, ST name).

Unlike the P1 event-flow axes, these tables carry a *state* that remains
informative until the next disclosure: the latest earnings forecast, the latest
express report, and the latest security name (ST status).  The loader turns
them into daily date x code matrices with the same point-in-time contract as
``p1_events``:

- the raw ``ts`` is the announcement/effective date;
- every matrix is forward-filled from the latest disclosure and then lagged by
  one trading day, so a value at date D only uses information disclosed on or
  before the previous trading day;
- values before the first disclosure are missing (``NaN``), never fabricated;
- ``is_st`` is zero for a name with no ST-related name change in the loaded
  history (absence of an ST event means "not ST").

The tables are complete over 2017-2025 in the current database:
``forecast`` (63k rows, 5,832 codes), ``express`` (17k rows, 4,261 codes) and
``namechange`` (4.9k rows, 3,908 codes).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Iterable

import numpy as np
import pandas as pd

from data_interface.common.questdb_client import QuestDBClient, sql_literal
from data_interface.common.trading_calendar import TradingCalendar


STATE_EVENT_FIELDS: dict[str, list[str]] = {
    "forecast": ["fc_p_change_mid", "fc_net_profit_mid", "fc_days_since"],
    "express": [
        "ex_revenue",
        "ex_n_income",
        "ex_total_assets",
        "ex_diluted_eps",
        "ex_yoy_net_profit",
    ],
    "namechange": ["is_st"],
}
ALL_STATE_FIELDS = sorted({name for fields in STATE_EVENT_FIELDS.values() for name in fields})


@dataclass
class StateEventBundle:
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
        raise ValueError("state event features require at least one code")
    return ",".join(sql_literal(code) for code in values)


def _query(
    db: Any,
    table: str,
    columns: list[str],
    codes: list[str],
    start: str,
    end: str,
) -> pd.DataFrame:
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
        return pd.DataFrame(columns=["ts", "code"] + columns)
    frame["ts"] = pd.to_datetime(frame["ts"], errors="coerce").dt.tz_localize(None).dt.normalize()
    frame = frame.dropna(subset=["ts"])
    frame["code"] = frame["code"].astype(str)
    for column in columns:
        if column in frame.columns and column != "name":
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame


def _state_frame(
    raw: pd.DataFrame,
    *,
    value: str,
    dates: pd.DatetimeIndex,
    codes: list[str],
    agg: str = "last",
) -> pd.DataFrame:
    """Forward-fill the latest disclosed ``value`` and lag it one trading day."""
    if raw.empty or value not in raw.columns:
        return pd.DataFrame(np.nan, index=dates, columns=codes)
    event = raw.dropna(subset=[value]).groupby(["ts", "code"])[value].agg(agg).unstack("code")
    if event.empty:
        return pd.DataFrame(np.nan, index=dates, columns=codes)
    event = event.reindex(columns=codes)
    state_dates = event.index.union(dates).sort_values()
    state = event.reindex(index=state_dates).ffill().reindex(index=dates)
    return state.shift(1)


def _days_since_frame(
    raw: pd.DataFrame,
    *,
    dates: pd.DatetimeIndex,
    codes: list[str],
) -> pd.DataFrame:
    if raw.empty:
        return pd.DataFrame(np.nan, index=dates, columns=codes)
    event = raw.groupby(["ts", "code"]).size().unstack("code")
    if event.empty:
        return pd.DataFrame(np.nan, index=dates, columns=codes)
    event = event.reindex(columns=codes)
    event_dates = pd.DataFrame(
        np.where(
            event.notna().to_numpy(),
            np.repeat(event.index.values[:, None], event.shape[1], axis=1),
            np.datetime64("NaT"),
        ),
        index=event.index,
        columns=event.columns,
    )
    last = event_dates.reindex(index=event.index.union(dates).sort_values()).ffill().reindex(index=dates)
    last = last.shift(1)
    days = (dates.values[:, None] - last.to_numpy()) / np.timedelta64(1, "D")
    return pd.DataFrame(days, index=dates, columns=codes)


def load_state_events(
    codes: list[str],
    start: str,
    end: str,
    *,
    db: Any | None = None,
) -> StateEventBundle:
    """Load forecast/express/ST state features over ``[start, end]``."""
    codes = sorted({str(code) for code in codes if str(code).strip()})
    if not codes:
        raise ValueError("state event features require at least one code")
    db = db or QuestDBClient()
    trim_start = _as_date(start)
    trim_end = _as_date(end)
    state_start = (trim_start - timedelta(days=400)).date().isoformat()
    dates_all = _trading_days(state_start, end)
    dates = dates_all[(dates_all >= trim_start) & (dates_all <= trim_end)]
    if dates.empty:
        raise ValueError(f"no trading days in state-event window {start}..{end}")

    bundle = StateEventBundle(dates=dates, codes=codes)
    try:
        forecast = _query(
            db,
            "forecast",
            ["p_change_min", "p_change_max", "net_profit_min", "net_profit_max"],
            codes,
            state_start,
            end,
        )
    except Exception:  # noqa: BLE001 - optional table
        forecast = pd.DataFrame()
    try:
        express = _query(
            db,
            "express",
            ["revenue", "n_income", "total_assets", "diluted_eps", "yoy_net_profit"],
            codes,
            state_start,
            end,
        )
    except Exception:  # noqa: BLE001 - optional table
        express = pd.DataFrame()
    try:
        # Name changes are effective-date rows; load the full history so an ST
        # period that starts before the window still has its latest state.
        namechange = _query(db, "namechange", ["name"], codes, "2000-01-01", end)
    except Exception:  # noqa: BLE001 - optional table
        namechange = pd.DataFrame()

    bundle.raw_rows = {
        "forecast": int(len(forecast)),
        "express": int(len(express)),
        "namechange": int(len(namechange)),
    }

    if not forecast.empty:
        forecast = forecast.copy()

        def _numeric(name: str) -> pd.Series:
            if name in forecast.columns:
                return pd.to_numeric(forecast[name], errors="coerce")
            return pd.Series(np.nan, index=forecast.index)

        forecast["fc_p_change_mid"] = (
            _numeric("p_change_min") + _numeric("p_change_max")
        ) / 2.0
        forecast["fc_net_profit_mid"] = (
            _numeric("net_profit_min") + _numeric("net_profit_max")
        ) / 2.0
    for name in STATE_EVENT_FIELDS["forecast"]:
        if name == "fc_days_since":
            bundle.frames[name] = _days_since_frame(forecast, dates=dates_all, codes=codes).loc[
                trim_start:trim_end
            ]
        else:
            bundle.frames[name] = _state_frame(
                forecast, value=name, dates=dates_all, codes=codes, agg="mean"
            ).loc[trim_start:trim_end]

    for name in STATE_EVENT_FIELDS["express"]:
        bundle.frames[name] = _state_frame(
            express, value=name.replace("ex_", "", 1), dates=dates_all, codes=codes
        ).loc[trim_start:trim_end]

    if not namechange.empty:
        names = namechange.copy()
        names["name"] = names.get("name", "").astype(str)
        event = names.groupby(["ts", "code"])["name"].last().unstack("code")
        if not event.empty:
            event = event.reindex(columns=codes)
            state_dates = event.index.union(dates_all).sort_values()
            state = event.reindex(index=state_dates).ffill().reindex(index=dates_all).shift(1)
            is_st = state.apply(
                lambda column: column.astype(str).str.contains("ST", case=False, na=False)
            )
            bundle.frames["is_st"] = is_st.astype(float).fillna(0.0).loc[trim_start:trim_end]
    if "is_st" not in bundle.frames:
        bundle.frames["is_st"] = pd.DataFrame(0.0, index=dates, columns=codes)
    return bundle
