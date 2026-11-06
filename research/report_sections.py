"""单因子报告的章节目录（profile 的唯一事实源）。

报告只消费这里的章节 ID；股票池、区间、horizon、cost 仍是运行参数。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ReportSection:
    id: str
    title: str
    description: str


SECTIONS: dict[str, ReportSection] = {
    "definition": ReportSection(
        "definition",
        "因子定义与样本",
        "因子公式、经济含义、样本池、选样漏斗、数据完整性与复现信息。",
    ),
    "timing": ReportSection(
        "timing",
        "时间维度：因子何时有效",
        "Rank IC、置信区间、ICIR、两类 t、滚动 IC、分年稳定性、市场状态与衰减。",
    ),
    "cross_section": ReportSection(
        "cross_section",
        "空间维度：因子在谁身上有效",
        "分层净值、多空收益、回撤、换手、成本、容量、中性化、增量性与可交易性。",
    ),
    "rating": ReportSection(
        "rating",
        "评级与定位",
        "100 分制评级、时间×空间象限、否决项、失效归因、结构化自检与重估条件。",
    ),
    "appendix": ReportSection(
        "appendix",
        "附录：指标明细与方法",
        "持有期明细、NW-t 与独立 t、方法说明、已知局限。",
    ),
}

DEFAULT_SECTIONS: tuple[str, ...] = tuple(SECTIONS)
QUICK_SECTIONS: tuple[str, ...] = ("definition", "timing", "rating")


def catalog() -> dict[str, ReportSection]:
    return SECTIONS


def validate(items: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    unknown = [item for item in items if item not in SECTIONS]
    if unknown:
        raise KeyError(f"未知报告章节: {unknown}；可用: {sorted(SECTIONS)}")
    return tuple(items)


__all__ = [
    "DEFAULT_SECTIONS",
    "QUICK_SECTIONS",
    "SECTIONS",
    "ReportSection",
    "catalog",
    "validate",
]
