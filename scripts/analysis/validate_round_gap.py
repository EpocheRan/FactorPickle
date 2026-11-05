"""验证 round_gap 是否有独立信息。

三件事：
1. 三个因子的横截面相关性（月度抽样，Spearman 均值）
2. round_gap 在 h=20 下的单独表现（IC / 净价差 / 评级）
3. 二因子回归（换手波动 + 跳空反转）的权重稳定性与表现
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
LOG = (OUT / "validate_progress.log").open("w", encoding="utf-8")

FACTORS = [
    "liquidity.turnover_std_bias_21_504",
    "momentum.rsrs",
    "reversal.round_gap",
]
HORIZON = 20


def log(msg: str) -> None:
    print(msg, flush=True)
    LOG.write(str(msg) + "\n")
    LOG.flush()


def forward_rank(close: pd.DataFrame, open_: pd.DataFrame, h: int) -> pd.DataFrame:
    c, o = close.to_numpy("float64"), open_.to_numpy("float64")
    fwd = np.full(c.shape, np.nan)
    for i in range(len(c) - h - 1):
        fwd[i] = c[i + h] / o[i + 1] - 1.0
    return pd.DataFrame(fwd, index=close.index, columns=close.columns).rank(axis=1, pct=True)


def main() -> int:
    log("加载面板…")
    panel = load_panel(None, None, "2019-01-01", None, universe="all_a")
    ranks = {}
    for fid in FACTORS:
        d = int(direction_of(fid))
        ranks[fid] = (price_factor_panel(fid, panel) * d).rank(axis=1, pct=True)
    dates = ranks[FACTORS[0]].index
    log(f"面板 {len(dates)} 天 × {panel.close.shape[1]} 只")

    # ---- 1) 相关性
    sample = dates[::20]
    pairs = []
    for a in FACTORS:
        for b in FACTORS:
            if a >= b:
                continue
            vals = []
            for dt in sample:
                x = ranks[a].loc[dt]
                y = ranks[b].loc[dt]
                ok = x.notna() & y.notna()
                if ok.sum() >= 200:
                    vals.append(x[ok].rank().corr(y[ok].rank()))   # 环境无 scipy，用秩+皮尔逊等价实现
            pairs.append((a.split(".")[-1], b.split(".")[-1], float(np.nanmean(vals))))
    log("\n横截面相关性（月度抽样均值）:")
    for a, b, v in pairs:
        log(f"  {a:<28} × {b:<28} ρ = {v:+.3f}")

    # ---- 2) round_gap 单独评估（h=20）
    rg = ranks["reversal.round_gap"]
    rep = metrics.build_report(
        rg, panel.close, panel.open, factor_id="reversal.round_gap",
        category="reversal", direction=1, primary_horizon=HORIZON, groups=5,
        market_cap=panel.market_cap, industry=panel.industry,
        amount=panel.amount, benchmark=panel.benchmark,
    )
    g = rating_mod.rate(rep)
    log(f"\n[round_gap 单独 h=20] IC {rep.ic_stats.loc[HORIZON, 'mean']:+.4f}"
        f"  t {rep.ic_stats.loc[HORIZON, 't_indep']:+.2f}"
        f"  净价差 {rep.spread_net['mean']*100:+.2f}%/期"
        f"  换手 {rep.turnover:.0%}  单调 {rep.monotonicity:+.2f}")
    log(f"  评级 {g['grade']} / {g['score']}")

    # ---- 3) 二因子回归
    two = ["liquidity.turnover_std_bias_21_504", "reversal.round_gap"]
    X = np.stack([(ranks[f] - 0.5).to_numpy("float64") for f in two])
    y = forward_rank(panel.close.loc[dates], panel.open.loc[dates], HORIZON).to_numpy("float64")
    grid = np.arange(0, len(dates) - HORIZON - 1, HORIZON)

    weights = {}
    for yr in sorted({dates[i].year for i in grid}):
        train = [i for i in grid if dates[i].year < yr]
        if len(train) < 20:
            continue
        rows = []
        for i in train:
            xi = X[:, i, :].T
            yi = y[i]
            ok = np.isfinite(xi).all(axis=1) & np.isfinite(yi)
            if ok.sum() >= 100:
                rows.append(np.column_stack([xi[ok], yi[ok]]))
        if not rows:
            continue
        data = np.vstack(rows)
        xx, yy = data[:, :-1], data[:, -1]
        beta = np.linalg.solve(xx.T @ xx + 1e-6 * np.eye(xx.shape[1]), xx.T @ yy)
        weights[yr] = beta
        log(f"  {yr} 二因子权重：{np.round(beta, 3).tolist()}")

    ols = np.full(X.shape[1:], np.nan)
    for i, ts in enumerate(dates):
        beta = weights.get(ts.year)
        ols[i] = np.nansum(X[:, i, :] * beta[:, None], axis=0) if beta is not None else np.nanmean(X[:, i, :], axis=0)
    two_df = pd.DataFrame(ols, index=dates, columns=panel.close.columns)
    two_df.to_pickle(OUT / "composite_ols2_h20.pkl")

    rep2 = metrics.build_report(
        two_df, panel.close, panel.open, factor_id="composite.ols2_h20",
        category="composite", direction=1, primary_horizon=HORIZON, groups=5,
        market_cap=panel.market_cap, industry=panel.industry,
        amount=panel.amount, benchmark=panel.benchmark,
    )
    g2 = rating_mod.rate(rep2)
    log(f"\n[二因子回归 h=20] IC {rep2.ic_stats.loc[HORIZON, 'mean']:+.4f}"
        f"  t {rep2.ic_stats.loc[HORIZON, 't_indep']:+.2f}"
        f"  净价差 {rep2.spread_net['mean']*100:+.2f}%/期  换手 {rep2.turnover:.0%}")
    log(f"  评级 {g2['grade']} / {g2['score']}")
    log("DONE")
    LOG.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
