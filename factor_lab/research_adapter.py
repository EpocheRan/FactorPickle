"""Adapter around the production research report evaluator.

Naming boundary: ``research.report.runner.generate`` is the Research Module
entrypoint in the research library, not the Factor Lab Runner.  This adapter
lets the Factor Lab Runner pass a raw Panel / data bundle in, then receives the
Research Result; it does not reimplement factor computation or metrics.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class ResearchRun:
    html: Path
    meta: dict[str, Any]
    report: Any
    rating: dict[str, Any]
    payload: dict[str, Any]


class ResearchReportAdapter:
    source = "research.report.runner.generate"

    def generate(self, factor_id: str, **kwargs: Any) -> ResearchRun:
        # Lazy import: the adapter is cheap to import in tests and CLI help.
        from research.report.runner import generate

        raw = generate(factor_id, **kwargs)
        return ResearchRun(
            html=Path(str(raw["html"])),
            meta=dict(raw.get("meta") or {}),
            report=raw.get("report"),
            rating=dict(raw.get("rating") or {}),
            payload=dict(raw),
        )


RESEARCH = ResearchReportAdapter()
