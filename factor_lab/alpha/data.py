"""Data loading and holdout governance for the Alpha DSL engine."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from factor_lab.core import config
from factor_lab.core.store import read_json, write_json_atomic
from factor_lab.alpha.compiler import AlphaContext
from factor_lab.runtime.data_interface_adapter import DataInterfaceGateway
from factor_lab.runtime.panel_builder import build_factor_panel


@dataclass
class AlphaDataset:
    context: AlphaContext
    codes: list[str]
    start: str
    end: str
    warmup_days: int
    data_manifest: dict[str, Any]

    def segment(self, start: str | None = None, end: str | None = None) -> "AlphaDataset":
        context = self.context
        index = context.close.index
        mask = pd.Series(True, index=index)
        if start is not None:
            mask &= index >= pd.Timestamp(start)
        if end is not None:
            mask &= index <= pd.Timestamp(end)
        sliced = AlphaContext(
            close=context.close.loc[mask],
            fields={name: frame.loc[mask] for name, frame in context.fields.items()},
            panel=context.panel,
            metadata={**context.metadata, "start": str(index[mask].min().date()) if mask.any() else None, "end": str(index[mask].max().date()) if mask.any() else None},
        )
        return AlphaDataset(
            context=sliced,
            codes=self.codes,
            start=start or self.start,
            end=end or self.end,
            warmup_days=self.warmup_days,
            data_manifest=self.data_manifest,
        )


def load_alpha_dataset(
    codes: Iterable[str],
    start: str,
    end: str,
    *,
    warmup_days: int = 300,
    gateway: DataInterfaceGateway | None = None,
    include_reference: bool = False,
) -> AlphaDataset:
    """Load market + every optional matrix through the DataInterfaceGateway."""
    code_list = sorted({str(code) for code in codes})
    if not code_list:
        raise ValueError("codes must not be empty")
    gateway = gateway or DataInterfaceGateway()
    built = build_factor_panel(
        gateway,
        {
            "factor_id": "alpha.panel",
            "category": "price",
            "data_contract": {"reference": include_reference},
        },
        code_list,
        start,
        end,
        warmup_days=warmup_days,
    )
    context = AlphaContext.from_panel(built.panel)
    manifest = {
        "services": built.manifest.services,
        "fields": built.manifest.fields,
        "codes": len(code_list),
        "start": start,
        "end": end,
        "rows": built.manifest.rows,
        "data_hash": built.manifest.data_hash,
        "category": built.category,
    }
    return AlphaDataset(
        context=context,
        codes=code_list,
        start=start,
        end=end,
        warmup_days=warmup_days,
        data_manifest=manifest,
    )


@dataclass
class HoldoutWindow:
    dev_start: str
    dev_end: str
    holdout_start: str
    holdout_end: str
    locked: bool = True

    def __post_init__(self) -> None:
        start = pd.Timestamp(self.dev_start)
        dev_end = pd.Timestamp(self.dev_end)
        holdout_start = pd.Timestamp(self.holdout_start)
        holdout_end = pd.Timestamp(self.holdout_end)
        if start > dev_end:
            raise ValueError(f"dev_start {self.dev_start} > dev_end {self.dev_end}")
        if holdout_start > holdout_end:
            raise ValueError(f"holdout_start {self.holdout_start} > holdout_end {self.holdout_end}")
        if self.locked and not (dev_end < holdout_start):
            raise ValueError(
                f"locked holdout overlaps development: dev_end={self.dev_end}, holdout_start={self.holdout_start}"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "factor_lab.alpha.holdout.v1",
            "dev_start": self.dev_start,
            "dev_end": self.dev_end,
            "holdout_start": self.holdout_start,
            "holdout_end": self.holdout_end,
            "locked": self.locked,
        }

    def assert_search_window(self, start: str, end: str) -> None:
        if not self.locked:
            return
        if pd.Timestamp(end) > pd.Timestamp(self.dev_end):
            raise ValueError(
                f"search end {end} exceeds development end {self.dev_end}; "
                f"holdout starts {self.holdout_start}"
            )
        if pd.Timestamp(start) < pd.Timestamp(self.dev_start):
            raise ValueError(f"search start {start} precedes development start {self.dev_start}")
        if not (pd.Timestamp(self.dev_end) < pd.Timestamp(self.holdout_start)):
            raise ValueError("locked holdout overlaps development window")


def default_holdout(end: str | None = None) -> HoldoutWindow:
    """Last 12 months are the locked holdout; everything before is development."""
    end_ts = pd.Timestamp(end or "2026-11-01")
    holdout_start = (end_ts - pd.DateOffset(months=12)).normalize()
    return HoldoutWindow(
        dev_start="2022-10-01",
        dev_end=(holdout_start - pd.Timedelta(days=1)).date().isoformat(),
        holdout_start=holdout_start.date().isoformat(),
        holdout_end=end_ts.date().isoformat(),
        locked=True,
    )


def holdout_path() -> Path:
    return config.ALPHA_POLICY_DIR / "holdout.json"


def save_holdout(window: HoldoutWindow) -> Path:
    path = holdout_path()
    existing = load_holdout()
    if existing is not None and existing.locked:
        current = existing.to_dict()
        incoming = window.to_dict()
        for key in ("dev_start", "dev_end", "holdout_start", "holdout_end"):
            if current.get(key) != incoming.get(key):
                raise ValueError(
                    f"holdout is locked as {current.get('holdout_start')}..{current.get('holdout_end')}; "
                    "changing it requires an explicit new lock decision"
                )
    write_json_atomic(path, window.to_dict())
    return path


def load_holdout() -> HoldoutWindow | None:
    raw = read_json(holdout_path())
    if not isinstance(raw, dict):
        return None
    return HoldoutWindow(
        dev_start=str(raw.get("dev_start")),
        dev_end=str(raw.get("dev_end")),
        holdout_start=str(raw.get("holdout_start")),
        holdout_end=str(raw.get("holdout_end")),
        locked=bool(raw.get("locked", True)),
    )
