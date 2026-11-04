"""口径校准网格：持有期 × 分组数 × 中性化 共 12 组。

面板只加载一次，后续用不同配置复算，避免 12 次全市场取数。
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np

from research.report import metrics
from research.report.panel import price_factor_panel
from research.report.panel_adapter import load_panel

FACTOR = "liquidity.turnover_std_bias_21_504"
START = "2020-01-01"
OUT = ROOT / "output" / "calibration" / "grid12.csv"

panel = load_panel("2017-11-01", 200, START, None, extras=("fin_valuation_daily",))
factor = price_factor_panel(FACTOR, panel)
print(f"panel {len(panel.symbols)} 只 / {len(panel.close)} 天，因子已算好", flush=True)

rows = []
for horizon in (5, 10, 20):
    for groups in (5, 10):
        for neutralize in (False, True):
            try:
                rep = metrics.build_report(
                    factor, panel.close, panel.open,
                    factor_id=FACTOR, category="liquidity", direction=-1,
                    groups=groups, primary_horizon=horizon,
                    market_cap=panel.market_cap, industry=panel.industry,
                    neutralize=neutralize, full=False,
                )
                ic = float(rep.ic_stats.loc[horizon, "mean"])
                gross = float((rep.spread_gross or {}).get("mean", np.nan))
                net = float((rep.spread_net or {}).get("mean", np.nan))
                sigma = float(getattr(rep, "cross_section_std", np.nan))
                expected = abs(ic) * 2.5 * sigma
                ratio = net / expected if expected > 1e-12 else np.nan
                rows.append({
                    "horizon": horizon, "groups": groups,
                    "neutralize": "size" if neutralize else "none",
                    "ic": round(ic, 4), "gross_pct": round(gross * 100, 3),
                    "net_pct": round(net * 100, 3), "turnover": round(float(rep.turnover), 3),
                    "ratio": round(ratio, 3) if np.isfinite(ratio) else "",
                })
                print(f"h={horizon} g={groups} n={neutralize} "
                      f"IC={ic:+.4f} net={net*100:+.3f}% ratio={ratio:.2f}", flush=True)
            except Exception as exc:  # noqa: BLE001
                print(f"h={horizon} g={groups} n={neutralize} 失败: {exc}", flush=True)

OUT.parent.mkdir(parents=True, exist_ok=True)
with OUT.open("w", newline="", encoding="utf-8") as fh:
    writer = csv.DictWriter(fh, fieldnames=list(rows[0]) if rows else [])
    writer.writeheader()
    writer.writerows(rows)
print(f"写入 {OUT}（{len(rows)} 组）")
