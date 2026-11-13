"""Unified factor-evaluation result objects."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .spec import MetricsSpec


@dataclass(frozen=True)
class FactorMetrics:
    """One cross-sectional evaluation result plus its audit identity."""

    spec: MetricsSpec
    metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = dict(self.metrics)
        payload["metrics_spec"] = {
            **self.spec.to_dict(),
            "config_hash": self.spec.config_hash(),
        }
        return payload
