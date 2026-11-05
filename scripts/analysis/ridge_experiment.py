"""Ridge 合成因子 vs 等权合成：walk-forward 样本外对比。

特征：5 个因子的横截面分位（每 20 个交易日一个观测点）
标签：未来 20 日收益的横截面分位
训练：年度 walk-forward（用当年之前的所有观测训练）
输出：OOS IC（Ridge vs 等权），逐期日志写入 log 文件便于实时查看。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd

from research.report.panel import price_factor_panel
from research.report.panel_adapter import load_panel

LOG = ROOT / "output" / "ai" / "ridge_progress.log"
LOG.parent.mkdir(parents=True, exist_ok=True)
_fh = LOG.open("w", encoding="utf-8")


def log(msg: str) -> None:
    line = f"[{pd.Timestamp.now().strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    _fh.write(line + "\n")
    _fh.flush()


FACTORS = [
    "liquidity.turnover_std_bias_21_504",
    "liquidity.turnover_std_bias_126_252",
    "momentum.rsrs",
    "risk.vol_21d",
    "reversal.ret_21d",
]
START = "2019-01-01"
HORIZON = 20
LAMBDA = 10.0


def forward_returns(close: pd.DataFrame, open_: pd.DataFrame, h: int) -> np.ndarray:
    """T 日信号 → T+1 开盘买 → T+h 收盘卖。"""
    c = close.to_numpy(dtype="float64")
    o = open_.to_numpy(dtype="float64")
    out = np.full(c.shape, np.nan)
    for i in range(len(c) - h - 1):
        out[i] = c[i + h] / o[i + 1] - 1.0
    return out


def cs_rank(a: np.ndarray) -> np.ndarray:
    """按行的横截面分位（0~1），NaN 保持 NaN。"""
    out = np.full(a.shape, np.nan)
    for i in range(a.shape[0]):
        row = a[i]
        ok = np.isfinite(row)
        if ok.sum() < 50:
            continue
        r = pd.Series(row[ok]).rank(pct=True).to_numpy()
        out[i, ok] = r
    return out


def rank_ic(pred: np.ndarray, fwd: np.ndarray) -> float:
    ok = np.isfinite(pred) & np.isfinite(fwd)
    if ok.sum() < 50:
        return np.nan
    a = pd.Series(pred[ok]).rank().to_numpy()
    b = pd.Series(fwd[ok]).rank().to_numpy()
    a -= a.mean()
    b -= b.mean()
    den = np.sqrt((a * a).sum() * (b * b).sum())
    return float((a * b).sum() / den) if den > 0 else np.nan


def main() -> int:
    log("加载面板（含预热）…")
    panel = load_panel(None, None, START, None, universe="all_a")
    dates = panel.close.index
    log(f"面板 {panel.close.shape[0]} 天 × {panel.close.shape[1]} 只")

    from factors.direction import direction_of

    feats = []
    for fid in FACTORS:
        d = int(direction_of(fid))
        log(f"计算因子 {fid}（方向 {d:+d}）")
        f = price_factor_panel(fid, panel).reindex(index=dates, columns=panel.close.columns)
        # 先按族先验定方向，再转横截面分位——否则等权合成会把负向因子当正向用
        feats.append(cs_rank(f.to_numpy(dtype="float64") * d))
    X_all = np.stack(feats, axis=-1)          # (dates, stocks, k)
    log(f"特征矩阵 {X_all.shape}")

    fwd = forward_returns(panel.close, panel.open, HORIZON)
    Y_all = cs_rank(fwd)
    log("标签完成，开始 walk-forward")

    idx = np.arange(0, len(dates) - HORIZON - 2, HORIZON)   # 每 20 天一个观测
    years = np.array([dates[i].year for i in idx])

    rows = []
    for year in sorted(set(years)):
        train = idx[years < year]
        test = idx[years == year]
        if len(train) < 12 or len(test) == 0:
            log(f"{year}: 训练样本 {len(train)} 不足，跳过")
            continue

        Xtr = X_all[train].reshape(-1, len(FACTORS))
        Ytr = Y_all[train].reshape(-1)
        ok = np.isfinite(Xtr).all(axis=1) & np.isfinite(Ytr)
        Xtr, Ytr = Xtr[ok], Ytr[ok]

        XtX = Xtr.T @ Xtr + LAMBDA * np.eye(len(FACTORS))
        beta = np.linalg.solve(XtX, Xtr.T @ Ytr)

        ics_ridge, ics_equal = [], []
        for i in test:
            Xt = X_all[i]
            yt = Y_all[i]
            valid = np.isfinite(Xt).all(axis=1)
            if valid.sum() < 100:
                continue
            pred = Xt[valid] @ beta
            ics_ridge.append(rank_ic(pred, yt[valid]))
            ics_equal.append(rank_ic(Xt[valid].mean(axis=1), yt[valid]))

        r = np.nanmean(ics_ridge) if ics_ridge else np.nan
        e = np.nanmean(ics_equal) if ics_equal else np.nan
        rows.append({"year": int(year), "n_test": len(ics_ridge),
                     "ic_ridge": r, "ic_equal": e,
                     "beta": np.round(beta, 3).tolist()})
        log(f"{year}: 训练 {len(train)} 期 | 测试 {len(ics_ridge)} 期 | "
            f"Ridge IC {r:+.4f} | 等权 IC {e:+.4f}")

    df = pd.DataFrame(rows)
    out = ROOT / "output" / "ai" / "ridge_vs_equal.csv"
    df.to_csv(out, index=False, encoding="utf-8-sig")

    log("=" * 60)
    log(f"样本外平均 IC  Ridge {df['ic_ridge'].mean():+.4f} | 等权 {df['ic_equal'].mean():+.4f}")
    log(f"逐年明细 → {out}")
    log("DONE")
    _fh.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
