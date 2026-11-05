"""三因子线性回归合成（h=20）。

对比两条线：
- EW3：三个 B 级因子等权（对照）
- OLS3：滚动训练线性权重（每年只用此前年份的样本拟合，weight 当年固定）

特征：三个因子按先验定方向后的横截面分位（0~1），中心化到 [-0.5, 0.5]
标签：未来 20 日收益的横截面分位
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
LOG = (OUT / "regression_progress.log").open("w", encoding="utf-8")

FACTORS = [
    "liquidity.turnover_std_bias_21_504",
    "momentum.rsrs",
    "reversal.round_gap",
]
START = "2019-01-01"
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
    panel = load_panel(None, None, START, None, universe="all_a")
    log(f"面板 {panel.close.shape[0]} 天 × {panel.close.shape[1]} 只")

    feats = []
    for fid in FACTORS:
        d = int(direction_of(fid))
        r = (price_factor_panel(fid, panel) * d).rank(axis=1, pct=True) - 0.5
        feats.append(r)
        log(f"  {fid}（方向 {d:+d}）覆盖 {int(r.notna().sum().sum()):,}")

    X = np.stack([f.to_numpy("float64") for f in feats])          # (k, T, N)
    # 因子面板已裁到评估起点（1899 天），收益与日历必须用同一索引，否则错位
    dates = feats[0].index
    y = forward_rank(panel.close.loc[dates], panel.open.loc[dates],
                     HORIZON).to_numpy("float64")
    grid = np.arange(0, len(dates) - HORIZON - 1, HORIZON)

    # ---- 逐年拟合（只用当年之前的样本）
    years = sorted({dates[i].year for i in grid})
    weights: dict[int, np.ndarray] = {}
    for yr in years:
        train = [i for i in grid if dates[i].year < yr]
        if len(train) < 24:
            continue
        rows = []
        for i in train:
            xi = X[:, i, :].T                     # (N, k)
            yi = y[i]
            ok = np.isfinite(xi).all(axis=1) & np.isfinite(yi)
            if ok.sum() >= 100:
                rows.append(np.column_stack([xi[ok], yi[ok]]))
        if not rows:
            continue
        data = np.vstack(rows)
        xx, yy = data[:, :-1], data[:, -1]
        # 轻微岭回归，避免共线
        beta = np.linalg.solve(xx.T @ xx + 1e-6 * np.eye(xx.shape[1]), xx.T @ yy)
        weights[yr] = beta
        log(f"  {yr} 拟合：训练 {len(train)} 期 / {len(data):,} 行 → "
            f"w = {np.round(beta / beta.sum(), 3).tolist()}")

    # ---- 应用：权重只用「当年之前」的拟合结果
    ols = np.full(X.shape[1:], np.nan)
    ew = np.nanmean(X, axis=0)
    for i, ts in enumerate(dates):
        beta = weights.get(ts.year)
        if beta is None:
            ols[i] = ew[i]                        # 2019–2020 无历史，退回等权
        else:
            ols[i] = np.nansum(X[:, i, :] * beta[:, None], axis=0)

    ols_df = pd.DataFrame(ols, index=dates, columns=panel.close.columns)
    ew_df = pd.DataFrame(ew, index=dates, columns=panel.close.columns)
    ols_df.to_pickle(OUT / "composite_ols3.pkl")
    ew_df.to_pickle(OUT / "composite_ew3.pkl")

    def evaluate(frame: pd.DataFrame, name: str) -> dict:
        rep = metrics.build_report(
            frame, panel.close, panel.open,
            factor_id=name, category="composite", direction=1,
            primary_horizon=HORIZON, groups=5,
            market_cap=panel.market_cap, industry=panel.industry,
            amount=panel.amount, benchmark=panel.benchmark,
        )
        g = rating_mod.rate(rep)
        log(f"\n[{name}] h={HORIZON}  IC {rep.ic_stats.loc[HORIZON, 'mean']:+.4f}  "
            f"t {rep.ic_stats.loc[HORIZON, 't_indep']:+.2f}  "
            f"净价差 {rep.spread_net['mean']*100:+.2f}%/期  换手 {rep.turnover:.0%}  "
            f"单调 {rep.monotonicity:+.2f}")
        log(f"  评级 {g['grade']} / {g['score']} （时间 {g['time_score']} × 空间 {g['space_score']} "
            f"× 兑现 {g.get('realization_score')}）")
        return {"name": name, "ic": rep.ic_stats.loc[HORIZON, "mean"],
                "net": rep.spread_net["mean"], "grade": g["grade"], "score": g["score"]}

    log("\n评估…")
    rows = [evaluate(ew_df, "composite.ew3_h20"), evaluate(ols_df, "composite.ols3_h20")]
    log("\n对照：单因子 turnover_std_bias_21_504 B/82.9（h=5 口径）")
    log("DONE")
    LOG.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
