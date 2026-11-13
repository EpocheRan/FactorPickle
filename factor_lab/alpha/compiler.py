"""Compile an Alpha DSL expression into a date x symbol factor matrix."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from factor_lab.alpha.expression import Expression, Node, parse_expression
from factor_lab.alpha.operators import OPS, WINDOW_OPERATORS
from factor_lab.alpha.validation import ValidationReport, validate


class CompileError(ValueError):
    pass


@dataclass
class AlphaContext:
    """All wide matrices available to an expression."""

    close: pd.DataFrame
    fields: dict[str, pd.DataFrame] = field(default_factory=dict)
    panel: Any | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_panel(cls, panel: Any, *, include_extras: bool = True) -> "AlphaContext":
        close = panel.close.copy()
        columns = list(close.columns)
        index = close.index
        fields: dict[str, pd.DataFrame] = {}

        def add(name: str, value: Any) -> None:
            if value is None:
                return
            key = str(name).lower()
            if isinstance(value, pd.DataFrame):
                frame = value.copy()
                frame = frame[~frame.index.duplicated(keep="last")]
                frame = frame.reindex(index=index, columns=columns)
                numeric = frame.apply(pd.to_numeric, errors="coerce")
                if key.startswith("fin_"):
                    # Financial statements are point-in-time events: carry the
                    # latest published value forward until the next report.
                    numeric = numeric.ffill()
                if numeric.notna().any().any():
                    fields[key] = numeric.astype(float)
            elif isinstance(value, pd.Series):
                series = value.reindex(index)
                if series.notna().any():
                    fields[key] = pd.DataFrame(
                        np.repeat(series.to_numpy()[:, None], len(columns), axis=1),
                        index=index,
                        columns=columns,
                    )

        for name in ("open", "high", "low", "volume", "amount", "turnover", "market_cap"):
            add(name, getattr(panel, name, None))
        add("close", close)
        add("close_raw", getattr(panel, "close_raw", None))
        add("open_raw", getattr(panel, "open_raw", None))
        add("high_raw", getattr(panel, "raw_high", None))
        add("low_raw", getattr(panel, "raw_low", None))
        add("returns", close.pct_change(fill_method=None))
        add("ret_1d", close.pct_change(fill_method=None))
        add("log_close", np.log(close.where(close > 0.0)))
        if include_extras:
            for name, value in (getattr(panel, "extras", None) or {}).items():
                add(name, value)
        return cls(
            close=close,
            fields=fields,
            panel=panel,
            metadata={
                "start": str(index.min().date()) if len(index) else None,
                "end": str(index.max().date()) if len(index) else None,
                "symbols": len(columns),
                "fields": sorted(fields),
            },
        )

    @classmethod
    def from_fields(cls, fields: dict[str, pd.DataFrame], close: pd.DataFrame | None = None) -> "AlphaContext":
        if close is None:
            if "close" not in fields:
                raise ValueError("close is required when not supplied")
            close = fields["close"]
        context = cls(close=close.copy(), fields={})
        columns = list(close.columns)
        index = close.index
        for name, frame in fields.items():
            context.fields[str(name).lower()] = frame.reindex(index=index, columns=columns).astype(float)
        context.metadata = {
            "start": str(index.min().date()) if len(index) else None,
            "end": str(index.max().date()) if len(index) else None,
            "symbols": len(columns),
            "fields": sorted(context.fields),
        }
        return context

    @property
    def field_names(self) -> list[str]:
        return sorted(self.fields)

    def field(self, name: str) -> pd.DataFrame:
        key = str(name).lower()
        if key not in self.fields:
            raise CompileError(f"unknown field: {name}")
        return self.fields[key]


def _eval_node(node: Node, context: AlphaContext) -> Any:
    if node.kind == "field":
        return context.field(node.name)
    if node.kind == "literal":
        if node.value is None:
            raise CompileError("literal without value")
        return float(node.value)
    spec = OPS.get(node.name)
    if spec is None:
        raise CompileError(f"unknown operator: {node.name}")
    if not (spec.min_args <= len(node.args) <= spec.max_args):
        raise CompileError(f"{node.name} expects {spec.min_args}..{spec.max_args} args")
    args = [_eval_node(arg, context) for arg in node.args]
    try:
        return spec.fn(*args)
    except Exception as exc:  # noqa: BLE001
        raise CompileError(f"{node.name} failed: {type(exc).__name__}: {exc}") from exc


def compile_expression(
    expression: Expression | str,
    context: AlphaContext,
    *,
    validate_first: bool = True,
    max_nodes: int = 120,
    max_depth: int = 12,
) -> tuple[pd.DataFrame, ValidationReport]:
    """Compile and return ``(factor, validation_report)``."""
    expr = parse_expression(expression) if isinstance(expression, str) else expression
    report = validate(
        expr,
        known_fields=context.field_names,
        max_nodes=max_nodes,
        max_depth=max_depth,
    )
    if validate_first and not report.ok:
        raise CompileError("; ".join(report.issues))
    value = _eval_node(expr.root, context)
    if isinstance(value, pd.Series):
        value = value.to_frame()
    if not isinstance(value, pd.DataFrame):
        value = pd.DataFrame(
            np.full(context.close.shape, float(value)),
            index=context.close.index,
            columns=context.close.columns,
        )
    factor = value.reindex(index=context.close.index, columns=context.close.columns)
    factor = factor.replace([np.inf, -np.inf], np.nan).astype(float)
    factor.name = str(expr)
    return factor, report


def rebalance_slice(factor: pd.DataFrame, *, step: int = 1, start: Any | None = None, end: Any | None = None) -> pd.DataFrame:
    frame = factor
    if start is not None:
        frame = frame.loc[frame.index >= pd.Timestamp(start)]
    if end is not None:
        frame = frame.loc[frame.index <= pd.Timestamp(end)]
    return frame.iloc[:: max(1, int(step))]
