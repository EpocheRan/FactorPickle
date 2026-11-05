"""因子合成 v1：等权合成 4 个已通过评估的因子，并给出与单因子的对比。

思路（来自 Ridge 实验的结论：等权不输优化权重）
1. 每个因子按族先验定方向，转横截面分位（0~1，越大越好）；
2. 等权平均；
3. 合成结果当作一个「新因子」走同一套评估（IC / 价差 / 换手 / 单调性 / 评级）。

输出：
- output/composite/summary.txt
- output/composite/composite_scores.pkl（每日每股合成分）
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd

from factors.direction import direction_of
from research.report import metrics, rating as rating_mod
from research.report.panel import price_factor_panel
from research.report.panel_adapter import load_panel

OUT = ROOT / "output" / "composite"
OUT.mkdir(parents=True, exist_ok=True)
LOG = (OUT / "progress.log").open("w", encoding="utf-8")

#: 来自聚类 + 29 个代表的重测结果：换手波动 B、趋势 B、跳空反转 B、成交额 C
CONSTITUENTS = [
    "liquidity.turnover_std_bias_21_504",
    "momentum.rsrs",
    "reversal.round_gap",
    "liquidity.amount_ma_20d",
]
START = "2019-01-01"
HORIZON = 5


def log(msg: str) -> None:
    line = str(msg)
    print(line, flush=True)
    LOG.write(line + "\n")
    LOG.flush()


def cs_rank(frame: pd.DataFrame) -> pd.DataFrame:
    return frame.rank(axis=1, pct=True)


def main() -> int:
    log("加载面板…")
    panel = load_panel(None, None, START, None, universe="all_a")
    log(f"面板 {panel.close.shape[0]} 天 × {panel.close.shape[1]} 只")

    parts = []
    for fid in CONSTITUENTS:
        d = int(direction_of(fid))
        raw = price_factor_panel(fid, panel)
        ranked = cs_rank(raw * d)
        parts.append(ranked)
        log(f"  {fid}（方向 {d:+d}）覆盖 {ranked.notna().sum().sum():,}")

    stack = np.stack([p.to_numpy(dtype="float64") for p in parts])
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        mean = np.nanmean(stack, axis=0)
    composite = pd.DataFrame(mean, index=parts[0].index, columns=parts[0].columns)
    # 至少一半成分有值才认可
    valid = np.isfinite(stack).sum(axis=0) >= max(2, len(parts) // 2)
    composite = composite.where(valid)

    coverage = composite.notna().sum().sum()
    log(f"合成分覆盖 {coverage:,}（约 {coverage / (composite.shape[0] * composite.shape[1]):.1%}）")

    composite.to_pickle(OUT / "composite_scores.pkl")

    log("按「新因子」评估…")
    report = metrics.build_report(
        composite, panel.close, panel.open,
        factor_id="composite.ew_v1", category="composite", direction=1,
        primary_horizon=HORIZON, groups=5,
        market_cap=panel.market_cap, industry=panel.industry,
        amount=panel.amount, benchmark=panel.benchmark,
    )
    grade = rating_mod.rate(report)

    lines = []
    for h in (5, 10, 20):
        row = report.ic_stats.loc[h]
        lines.append(f"  h={h:>2}日  IC {row['mean']:+.4f}  t {row['t_indep']:+.2f}")
    lines.append("")
    lines.append(f"  毛价差 {report.spread_gross['mean']*100:+.2f}%/期  "
                 f"净价差 {report.spread_net['mean']*100:+.2f}%/期")
    lines.append(f"  单调性 {report.monotonicity:+.2f}  换手 {report.turnover:.0%}  "
                 f"覆盖率 {report.coverage:.0%}")
    lines.append("")
    lines.append(f"  评级 {grade['grade']} / {grade['score']}  "
                 f"（时间 {grade['time_score']} × 空间 {grade['space_score']} "
                 f"× 兑现 {grade.get('realization_score')}）")

    text = "\n".join(lines)
    log("\n合成因子评估结果")
    log(text)
    (OUT / "summary.txt").write_text(text, encoding="utf-8")

    # 与最佳单因子对比
    log("\n对照（单因子最好成绩）：turnover_std_bias_21_504 B/82.9，rsrs B/77.1，round_gap B/72.3")
    log("DONE")
    LOG.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
