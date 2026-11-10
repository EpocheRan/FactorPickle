"""The only data door available to the new Runner.

The Runner is allowed to ask for any *service* exposed by ``data_interface``;
it is not allowed to import ``research.data.db``, open a QuestDB connection, or
write SQL.  This adapter is deliberately small: it wraps the public
``data_interface`` runtime getters and records enough provenance for a
``data_manifest.json``.
"""

from __future__ import annotations

import hashlib
import importlib
from dataclasses import dataclass, field
import os
from typing import Any, Callable

import pandas as pd


class DataInterfaceUnavailable(RuntimeError):
    """The data_interface package or one of its services is unavailable."""


class DataGap(RuntimeError):
    """A requested field/service is not exposed by data_interface."""


@dataclass
class DataManifest:
    schema_version: str = "factor_lab.data_manifest.v1"
    source: str = "data_interface"
    services: list[str] = field(default_factory=list)
    fields: list[str] = field(default_factory=list)
    codes: list[str] = field(default_factory=list)
    start: str = ""
    end: str = ""
    rows: int = 0
    data_hash: str = ""
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source": self.source,
            "services": list(self.services),
            "fields": list(self.fields),
            "codes": list(self.codes),
            "start": self.start,
            "end": self.end,
            "rows": self.rows,
            "data_hash": self.data_hash,
            "error": self.error,
        }


def _frame_hash(frames: dict[str, pd.DataFrame]) -> str:
    digest = hashlib.sha256()
    for code in sorted(frames):
        frame = frames[code]
        digest.update(str(code).encode("utf-8"))
        if isinstance(frame, pd.DataFrame) and not frame.empty:
            digest.update(frame.to_csv(index=False).encode("utf-8"))
    return digest.hexdigest()


@dataclass
class DataBundle:
    """Typed data payload shared by PanelBuilder and ML Engine."""

    kind: str
    frames: dict[str, pd.DataFrame] = field(default_factory=dict)
    frame: pd.DataFrame | None = None
    fields: list[str] = field(default_factory=list)
    manifest: DataManifest = field(default_factory=DataManifest)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "frames": sorted(self.frames),
            "rows": int(len(self.frame)) if self.frame is not None else 0,
            "fields": list(self.fields),
            "manifest": self.manifest.to_dict(),
        }


class DataInterfaceGateway:
    """Thin, testable gateway over data_interface's public runtime services."""

    def __init__(
        self,
        *,
        market_loader_factory: Callable[[], Any] | None = None,
        financial_factory: Callable[[], Any] | None = None,
        moneyflow_factory: Callable[[], Any] | None = None,
        disclosure_factory: Callable[[], Any] | None = None,
        calendar_factory: Callable[[], Any] | None = None,
        client_factory: Callable[[], Any] | None = None,
    ) -> None:
        self._market_loader_factory = market_loader_factory
        self._financial_factory = financial_factory
        self._moneyflow_factory = moneyflow_factory
        self._disclosure_factory = disclosure_factory
        self._calendar_factory = calendar_factory
        self._client_factory = client_factory
        self._market: Any | None = None
        self._client: Any | None = None

    # ---------------------------------------------------------------- imports
    @staticmethod
    def _runtime() -> Any:
        try:
            return importlib.import_module("data_interface.api.runtime")
        except Exception as exc:  # noqa: BLE001
            raise DataInterfaceUnavailable(
                f"data_interface is not importable: {type(exc).__name__}: {exc}"
            ) from exc

    def _get_market(self) -> Any:
        if self._market is None:
            if self._market_loader_factory is not None:
                self._market = self._market_loader_factory()
            else:
                self._market = self._runtime().get_market_loader()
        return self._market

    def _get_client(self) -> Any:
        """Low-level QuestDB client, only used by typed gateway bundles.

        Runner/PanelBuilder never import this client directly.  The raw query
        helpers below are the gateway's fallback for datasets that
        data_interface does not yet expose as a service (margin, industry,
        regime, materialized factor values).
        """

        if self._client is None:
            if self._client_factory is not None:
                self._client = self._client_factory()
            else:
                from data_interface.common.questdb_client import QuestDBClient

                self._client = QuestDBClient(connect=False)
        return self._client

    def query_frame(self, sql: str, params: Any | None = None) -> pd.DataFrame:
        try:
            frame = self._get_client().query_dataframe(sql, params)
        except Exception as exc:  # noqa: BLE001
            raise DataInterfaceUnavailable(
                f"data_interface query failed: {type(exc).__name__}: {exc}"
            ) from exc
        return frame if isinstance(frame, pd.DataFrame) else pd.DataFrame()

    def _get(self, name: str) -> Any:
        if name == "market":
            return self._get_market()
        if name == "questdb":
            return self._get_client()
        factory = {
            "financial": self._financial_factory,
            "moneyflow": self._moneyflow_factory,
            "disclosure": self._disclosure_factory,
            "calendar": self._calendar_factory,
        }.get(name)
        if factory is not None:
            return factory()
        runtime = self._runtime()
        getters = {
            "financial": "get_financial_service",
            "moneyflow": "get_moneyflow_service",
            "disclosure": "get_disclosure_service",
            "calendar": "get_trading_calendar",
        }
        try:
            return getattr(runtime, getters[name])()
        except Exception as exc:  # noqa: BLE001
            raise DataInterfaceUnavailable(
                f"data_interface.{name} is unavailable: {type(exc).__name__}: {exc}"
            ) from exc

    # ---------------------------------------------------------------- status
    def status(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "schema_version": "factor_lab.data_interface_status.v1",
            "available": True,
            "services": [],
            "error": "",
        }
        try:
            runtime = self._runtime()
            result["module"] = getattr(runtime, "__name__", "data_interface.api.runtime")
            for key in ("market", "financial", "moneyflow", "disclosure", "calendar"):
                try:
                    self._get(key)
                    result["services"].append(key)
                except Exception as exc:  # noqa: BLE001
                    result.setdefault("service_errors", {})[key] = f"{type(exc).__name__}: {exc}"
            result["available"] = "market" in result["services"]
        except Exception as exc:  # noqa: BLE001
            result["available"] = False
            result["error"] = f"{type(exc).__name__}: {exc}"
        return result

    # ---------------------------------------------------------------- market
    def ohlc(
        self,
        codes: str | list[str],
        start: str,
        end: str,
        *,
        freq: str = "d",
        adjust: str = "qfq",
    ) -> tuple[dict[str, pd.DataFrame], DataManifest]:
        """Read OHLC through data_interface only.

        ``qfq`` matches the research loader's ``hfq_*`` convention (scale by
        last adj_factor), so a Runner panel and legacy panel are comparable.
        """

        try:
            source_priority = [] if os.environ.get("FACTOR_LAB_LOCAL_ONLY", "1") == "1" else None
            frame = self._get_market().ohlc(
                codes, start, end, freq=freq, adjust=adjust, source_priority=source_priority
            )
        except Exception as exc:  # noqa: BLE001
            raise DataInterfaceUnavailable(
                f"market.ohlc failed: {type(exc).__name__}: {exc}"
            ) from exc
        frames = {str(k): v for k, v in (frame or {}).items() if isinstance(v, pd.DataFrame)}
        if not frames or all(item.empty for item in frames.values()):
            raise DataGap(f"market.ohlc returned no data for {codes} {start}..{end}")
        manifest = DataManifest(
            services=["market"],
            fields=["open", "high", "low", "close", "volume", "amount"],
            codes=sorted(frames),
            start=str(start),
            end=str(end),
            rows=int(sum(len(item) for item in frames.values())),
            data_hash=_frame_hash(frames),
        )
        return frames, manifest

    def market_bundle(
        self,
        codes: str | list[str],
        start: str,
        end: str,
        *,
        warmup_days: int = 800,
        adjust: str = "qfq",
    ) -> DataBundle:
        fetch_start = (
            pd.Timestamp(start) - pd.Timedelta(days=int(warmup_days))
        ).date().isoformat()
        frames, manifest = self.ohlc(codes, fetch_start, end, adjust=adjust)
        return DataBundle(
            kind="market",
            frames=frames,
            fields=list(manifest.fields),
            manifest=manifest,
        )

    def flow_bundle(
        self,
        codes: list[str],
        start: str,
        end: str,
    ) -> DataBundle:
        """Best-effort moneyflow bundle through data_interface.

        Margin data is not exposed by this service in the current environment;
        callers get only columns that exist. Missing required columns become a
        DataGap in PanelBuilder rather than a silent fallback.
        """

        service = self._get("moneyflow")
        if not hasattr(service, "get_by_code"):
            raise DataGap("moneyflow service does not expose get_by_code")
        mapping = {
            "buy_sm_amount": "mf_buy_sm",
            "sell_sm_amount": "mf_sell_sm",
            "buy_lg_amount": "mf_buy_lg",
            "sell_lg_amount": "mf_sell_lg",
            "buy_elg_amount": "mf_buy_elg",
            "sell_elg_amount": "mf_sell_elg",
            "net_mf_amount": "mf_net_amount",
        }
        by_column: dict[str, dict[str, pd.Series]] = {}
        rows = 0
        for code in codes:
            try:
                raw = service.get_by_code(str(code), start, end)
            except Exception as exc:  # noqa: BLE001
                raise DataInterfaceUnavailable(
                    f"moneyflow.get_by_code({code}) failed: {type(exc).__name__}: {exc}"
                ) from exc
            if not isinstance(raw, pd.DataFrame) or raw.empty:
                continue
            frame = raw.copy()
            ts_col = "ts" if "ts" in frame.columns else ("date" if "date" in frame.columns else None)
            if ts_col:
                frame[ts_col] = pd.to_datetime(frame[ts_col], errors="coerce").dt.tz_localize(None)
                frame = frame.set_index(ts_col)
            frame.index = pd.to_datetime(frame.index, errors="coerce").tz_localize(None)
            frame = frame[~frame.index.isna()]
            for column in frame.columns:
                name = str(column).lower()
                if name in {"code", "symbol"}:
                    continue
                target = mapping.get(name, name)
                values = pd.to_numeric(frame[column], errors="coerce")
                if values.notna().any():
                    by_column.setdefault(target, {})[str(code)] = values
                    rows += int(values.notna().sum())
        if not by_column:
            raise DataGap(f"moneyflow returned no usable fields for {codes} {start}..{end}")
        extras = {
            name: pd.DataFrame(series).sort_index()
            for name, series in by_column.items()
        }
        manifest = DataManifest(
            services=["moneyflow"],
            fields=sorted(extras),
            codes=list(codes),
            start=str(start),
            end=str(end),
            rows=rows,
            data_hash=_frame_hash(extras),
        )
        return DataBundle(kind="flow", frames=extras, fields=sorted(extras), manifest=manifest)

    def ml_frame(
        self,
        codes: list[str],
        start: str,
        end: str,
        *,
        fields: list[str] | None = None,
        warmup_days: int = 800,
    ) -> DataBundle:
        """Long-form market frame for non-DL ML Engine experiments."""

        bundle = self.market_bundle(codes, start, end, warmup_days=warmup_days)
        wanted = fields or ["open", "high", "low", "close", "volume", "amount"]
        rows: list[pd.DataFrame] = []
        for code, raw in bundle.frames.items():
            frame = raw.copy()
            ts_col = "ts" if "ts" in frame.columns else ("date" if "date" in frame.columns else None)
            if ts_col:
                frame[ts_col] = pd.to_datetime(frame[ts_col], errors="coerce").dt.tz_localize(None)
                frame = frame.set_index(ts_col)
            frame.index = pd.to_datetime(frame.index, errors="coerce").tz_localize(None)
            frame = frame[~frame.index.isna()]
            frame.columns = [str(column).lower() for column in frame.columns]
            keep = [field for field in wanted if field in frame.columns]
            if not keep:
                continue
            item = frame[keep].copy()
            item["date"] = item.index
            item["asset"] = str(code)
            rows.append(item.reset_index(drop=True))
        if not rows:
            raise DataGap(f"market bundle has none of the requested ML fields: {wanted}")
        long = pd.concat(rows, ignore_index=True)
        manifest = DataManifest(
            services=["market"],
            fields=list(wanted),
            codes=list(codes),
            start=str(start),
            end=str(end),
            rows=int(len(long)),
            data_hash=_frame_hash({"ml": long.set_index("date") if "date" in long else long}),
        )
        return DataBundle(kind="ml", frame=long, fields=list(wanted), manifest=manifest)

    @staticmethod
    def _code_literals(codes: list[str]) -> str:
        return ",".join("'" + str(code).replace("'", "''") + "'" for code in codes)

    def _extra_bundle(
        self,
        *,
        kind: str,
        table: str,
        mapping: dict[str, str],
        codes: list[str],
        start: str,
        end: str,
    ) -> DataBundle:
        columns = ", ".join(["ts", "code"] + list(mapping))
        sql = (
            f"select {columns} from {table} "
            f"where code in ({self._code_literals(codes)}) "
            f"and ts between '{start}' and '{end}'"
        )
        frame = self.query_frame(sql)
        if frame.empty:
            raise DataGap(f"{table} returned no rows for {len(codes)} codes")
        if frame.index.name == "ts":
            frame = frame.reset_index()
        if "ts" in frame.columns:
            frame["ts"] = pd.to_datetime(frame["ts"], errors="coerce").dt.tz_localize(None)
        extras: dict[str, pd.DataFrame] = {}
        for source, target in mapping.items():
            if source not in frame.columns:
                continue
            pivoted = frame.pivot_table(index="ts", columns="code", values=source, aggfunc="last")
            if pivoted.notna().any().any():
                extras[target] = pivoted
        if not extras:
            raise DataGap(f"{table} has none of the requested fields: {sorted(mapping)}")
        manifest = DataManifest(
            services=[kind],
            fields=sorted(extras),
            codes=list(codes),
            start=str(start),
            end=str(end),
            rows=int(len(frame)),
            data_hash=_frame_hash(extras),
        )
        return DataBundle(kind=kind, frames=extras, fields=sorted(extras), manifest=manifest)

    def margin_bundle(self, codes: list[str], start: str, end: str) -> DataBundle:
        return self._extra_bundle(
            kind="margin",
            table="margin_detail",
            mapping={"rzye": "margin_rzye", "rzmre": "margin_rzmre", "rqye": "margin_rqye", "rqyl": "margin_rqyl"},
            codes=codes,
            start=start,
            end=end,
        )

    def valuation_bundle(self, codes: list[str], start: str, end: str) -> DataBundle:
        # Load every canonical valuation field the table exposes.  The bundle
        # is a date x code matrix; its columns are factor inputs, not a fixed
        # market-cap-only contract.
        try:
            from data_interface.financial.fields import VALUATION_COLUMNS

            mapping = {
                name: name
                for name in VALUATION_COLUMNS
                if name not in {"ts", "code", "period_end", "publish_date"}
            }
        except Exception:
            mapping = {"market_cap": "total_mv", "float_market_cap": "float_mv"}
        mapping["market_cap"] = "total_mv"
        mapping["float_market_cap"] = "float_mv"
        return self._extra_bundle(
            kind="valuation",
            table="fin_valuation_daily",
            mapping=mapping,
            codes=codes,
            start=start,
            end=end,
        )

    def industry_map(self) -> pd.Series:
        frame = self.query_frame(
            "select code, last(board_name) as name from map_stock_board "
            "where is_primary = true group by code"
        )
        if frame.empty:
            return pd.Series(dtype=object)
        return pd.Series(frame["name"].to_numpy(), index=frame["code"].astype(str)).dropna()

    def regime_series(self, dates: pd.DatetimeIndex) -> pd.Series | None:
        frame = self.query_frame(
            "select ts, close from ohlcv_1d where type='idx' and code='399006' order by ts"
        )
        if frame.empty:
            return None
        if frame.index.name == "ts":
            frame = frame.reset_index()
        index = pd.to_datetime(frame["ts"], utc=True).dt.tz_convert(None).dt.normalize()
        close = pd.Series(pd.to_numeric(frame["close"], errors="coerce").to_numpy(), index=index)
        close = close[~close.index.duplicated()].sort_index()
        ma = close.rolling(200, min_periods=120).mean()
        regime = pd.Series("neutral", index=close.index, dtype=object)
        regime[close > ma * 1.03] = "bull"
        regime[close < ma * 0.97] = "bear"
        regime[ma.isna()] = "neutral"
        return regime.reindex(dates).ffill()

    def stored_factor_bundle(
        self,
        factor_id: str,
        codes: list[str],
        start: str,
        end: str,
    ) -> DataBundle:
        sql = (
            "select ts, code, value from factor_value_daily "
            f"where factor_id='{factor_id.replace(chr(39), chr(39) * 2)}' "
            f"and ts between '{start}' and '{end}' "
            f"and code in ({self._code_literals(codes)})"
        )
        frame = self.query_frame(sql)
        if frame.empty:
            raise DataGap(f"factor_value_daily has no values for {factor_id}")
        if frame.index.name == "ts":
            frame = frame.reset_index()
        if "ts" in frame.columns:
            frame["ts"] = pd.to_datetime(frame["ts"], errors="coerce").dt.tz_localize(None)
        frame["value"] = pd.to_numeric(frame["value"], errors="coerce")
        wide = frame.pivot_table(index="ts", columns="code", values="value", aggfunc="last")
        manifest = DataManifest(
            services=["factor_value_daily"],
            fields=[factor_id],
            codes=list(codes),
            start=str(start),
            end=str(end),
            rows=int(len(frame)),
            data_hash=_frame_hash({"values": wide}),
        )
        return DataBundle(kind="stored_factor", frames={factor_id: wide}, manifest=manifest)


    def financial(self, code: str, *args: Any, **kwargs: Any) -> Any:
        """Delegate to FinancialDataService; no SQL in Factor Lab."""

        return self._get("financial").get_statements(code, *args, **kwargs)

    def moneyflow(self, code: str, *args: Any, **kwargs: Any) -> Any:
        """Delegate to MoneyflowService/repository."""

        return self._get_moneyflow().query_by_code(code, *args, **kwargs)

    def disclosure(self, *args: Any, **kwargs: Any) -> Any:
        return self._get("disclosure").query(*args, **kwargs)

    def trading_days(self, start: str, end: str) -> list[str]:
        calendar = self._get("calendar")
        for name in ("trading_days", "get_trading_days", "between"):
            method = getattr(calendar, name, None)
            if callable(method):
                return [str(item) for item in method(start, end)]
        raise DataGap("data_interface calendar does not expose trading_days(start, end)")
