"""报告数据面板的运行参数。

股票池、区间、horizon、成本、分位等运行参数集中在这里；
profile 只描述报告章节，不承担这些运行参数。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PanelSpec:
    """报告数据面板的统一参数。

    ``universe`` 三种写法：

    - ``index:000905``：中证500 的 PIT 成分（``index_weight`` 逐期快照）
    - ``pool:2017-11-01:200``：指定日期成交额前 N（历史口径，含幸存者偏差，仅作对照）
    - ``explicit:600519,000001``：显式列表
    """

    universe: str = "all_a"
    start: str = "2019-01-01"
    end: str | None = None
    #: 前向收益 = close[t+h] / open[t+1] - 1
    horizon: int = 20
    #: 单边成本（bp），净价差 = 毛价差 - 2 * cost_bps / 1e4
    cost_bps: float = 10.0
    #: 多空分位比例（0.2 = 前后各 20%）
    quantile: float = 0.2
    #: 单日最少标的数，不足不计入 IC
    min_names: int = 50
    #: 有效交易日覆盖率下限，低于此值判「证据不足」
    min_coverage: float = 0.6
    #: 取数预热自然日（保证 252 日窗口因子在 start 处已可用）
    warmup_days: int = 800
    #: 额外数据列（margin_detail / moneyflow_daily / fin_valuation_daily）
    extras: tuple[str, ...] = ()
    #: 相关性参照系（默认库内冠军）
    reference: str = "liquidity.turnover_std_bias_42_252"

    #: 判定阈值（全库统一，不按族调整）
    min_ic: float = 0.02
    min_t: float = 2.0
    max_rho_reference: float = 0.9

    def universe_label(self) -> str:
        return self.universe

    def to_payload(self) -> dict:
        return {
            "universe": self.universe,
            "start": self.start,
            "end": self.end,
            "horizon": self.horizon,
            "cost_bps": self.cost_bps,
            "quantile": self.quantile,
            "min_names": self.min_names,
            "min_coverage": self.min_coverage,
            "warmup_days": self.warmup_days,
            "extras": list(self.extras),
            "reference": self.reference,
            "min_ic": self.min_ic,
            "min_t": self.min_t,
            "max_rho_reference": self.max_rho_reference,
        }
