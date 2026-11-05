"""行业映射：报告层可选增强，失败时返回空。"""

from __future__ import annotations

import pandas as pd


def _norm(code) -> str:
    text = str(code).strip()
    if text.endswith(".0"):
        text = text[:-2]
    return text.zfill(6) if text.isdigit() else text


def load_industry_map() -> dict[str, str]:
    try:
        from data_interface.common.questdb_client import QuestDBClient

        client = QuestDBClient(connect=True)
        frame = client.query_dataframe(
            "select code, last(industry) as industry from stock_basic_info "
            "where industry is not null group by code"
        )
    except Exception:  # noqa: BLE001 - 报告增强失败不影响主报告
        return {}
    if frame is None or frame.empty:
        return {}
    return {
        _norm(code): str(industry)
        for code, industry in zip(frame["code"], frame["industry"])
        if industry is not None and str(industry).strip()
    }


def load_index_close(code: str, start, end) -> pd.Series:
    try:
        from data_interface.common.questdb_client import QuestDBClient

        client = QuestDBClient(connect=True)
        frame = client.query_dataframe(
            "select ts, close from ohlcv_1d "
            "where type = 'idx' and code = %s and ts >= %s and ts <= %s order by ts",
            [str(code), str(pd.Timestamp(start)), str(pd.Timestamp(end))],
        )
    except Exception:  # noqa: BLE001
        return pd.Series(dtype="float64")
    if frame is None or frame.empty:
        return pd.Series(dtype="float64")
    if "ts" not in frame.columns:
        frame = frame.reset_index()
    frame["ts"] = pd.to_datetime(frame["ts"], errors="coerce")
    frame = frame.dropna(subset=["ts"]).set_index("ts")
    return pd.to_numeric(frame["close"], errors="coerce").dropna()


def industry_exposure(
    positions: pd.DataFrame | None,
    equity: pd.DataFrame,
    industry_map: dict[str, str],
) -> list[dict]:
    if positions is None or positions.empty or not industry_map or "date" not in positions.columns:
        return []
    pos = positions.copy()
    pos["date"] = pd.to_datetime(pos["date"], errors="coerce").dt.normalize()
    last_date = pos["date"].max()
    latest = pos[pos["date"] == last_date].copy()
    if latest.empty:
        return []
    value_col = "market_value" if "market_value" in latest.columns else "close"
    latest["_value"] = pd.to_numeric(latest[value_col], errors="coerce").fillna(0.0)
    latest["_industry"] = latest["symbol"].astype(str).map(lambda c: industry_map.get(_norm(c), "未映射"))
    eq = equity.copy()
    eq["date"] = pd.to_datetime(eq["date"], errors="coerce").dt.normalize()
    total = float(eq.loc[eq["date"] == last_date, "equity"].sum())
    if total <= 0:
        total = float(latest["_value"].sum())
    grouped = latest.groupby("_industry")["_value"].sum().sort_values(ascending=False)
    return [
        {
            "industry": str(name),
            "market_value": float(value),
            "weight_pct": float(value / total * 100.0),
        }
        for name, value in grouped.items()
    ]


def industry_attribution(
    trades: pd.DataFrame | None,
    industry_map: dict[str, str],
) -> list[dict]:
    if trades is None or trades.empty or not industry_map or "symbol" not in trades.columns:
        return []
    frame = trades.copy()
    frame["_industry"] = frame["symbol"].astype(str).map(lambda c: industry_map.get(_norm(c), "未映射"))
    frame["pnl"] = pd.to_numeric(frame["pnl"], errors="coerce").fillna(0.0)
    grouped = (
        frame.groupby("_industry")
        .agg(pnl=("pnl", "sum"), trades=("pnl", "size"))
        .sort_values("pnl", ascending=False)
    )
    return [
        {"industry": str(name), "pnl": float(row.pnl), "trades": int(row.trades)}
        for name, row in grouped.iterrows()
    ]
