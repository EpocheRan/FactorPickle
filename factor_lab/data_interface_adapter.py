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
    ) -> None:
        self._market_loader_factory = market_loader_factory
        self._financial_factory = financial_factory
        self._moneyflow_factory = moneyflow_factory
        self._disclosure_factory = disclosure_factory
        self._calendar_factory = calendar_factory
        self._market: Any | None = None

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

    def _get(self, name: str) -> Any:
        if name == "market":
            return self._get_market()
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
        adjust: str = "hfq",
    ) -> tuple[dict[str, pd.DataFrame], DataManifest]:
        """Read OHLC through data_interface only.

        Missing/empty result becomes ``DataGap``; we never fall back to a raw
        database query.
        """

        try:
            frame = self._get_market().ohlc(
                codes, start, end, freq=freq, adjust=adjust
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
        adjust: str = "hfq",
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
                values = pd.to_numeric(frame[column], errors="coerce")
                if values.notna().any():
                    by_column.setdefault(name, {})[str(code)] = values
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
