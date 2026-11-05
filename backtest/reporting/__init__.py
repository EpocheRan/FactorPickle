"""回测报告：数据契约、指标、回合交易与 HTML 渲染。"""

from .data import ReportDataBuilder
from .output import HtmlReportOutput
from .render import render_report
from .trades import build_round_trips

__all__ = [
    "HtmlReportOutput",
    "ReportDataBuilder",
    "build_round_trips",
    "render_report",
]
