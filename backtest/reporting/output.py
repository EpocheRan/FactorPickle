"""HTML 报告输出。"""

from __future__ import annotations

import json
from pathlib import Path

from backtest.domain.models import BacktestResult, OutputArtifact

from .data import ReportDataBuilder
from .render import render_report


class HtmlReportOutput:
    name = "html"

    def __init__(self, output_dir: str | Path = "output/backtest"):
        self.output_dir = Path(output_dir)

    def emit(self, result: BacktestResult) -> list[OutputArtifact]:
        run_dir = self.output_dir / result.run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        data = ReportDataBuilder(result).build()
        json_path = run_dir / "report_data.json"
        json_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        html_path = run_dir / "report.html"
        html_path.write_text(render_report(data), encoding="utf-8")
        return [
            OutputArtifact(kind="file", path=html_path, rows=1),
            OutputArtifact(kind="file", path=json_path, rows=1),
        ]
