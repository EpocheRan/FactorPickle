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
