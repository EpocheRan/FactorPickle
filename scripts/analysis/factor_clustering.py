"""218 个因子的相关性聚类（只读，不改任何数据）。

做法：
1. 取 6 个月末交易日作为采样点；
2. 在线因子在完整面板上计算后取这 6 天；预计算因子从 factor_value_daily 直接读；
3. 每天做横截面排名 → 因子两两 Spearman 相关 → 对 6 天取平均；
4. 层次聚类（距离 = 1 − ρ），按阈值切组，每组选代表。

输出：
- output/clustering/corr_matrix.csv
- output/clustering/groups.md
- output/clustering/progress.log
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd
import requests

from factors.ops.delta_volume import DELTA_FACTORS
from factors.ops.flow import FLOW_FACTORS
from factors.ops.lowfreq import LOWFREQ_FACTORS
from factors.ops.price_factors import ALL_NAMES as PRICE_NAMES
from research.report.panel import flow_factor_panel, price_factor_panel
from research.report.panel_adapter import load_panel

OUT = ROOT / "output" / "clustering"
OUT.mkdir(parents=True, exist_ok=True)
LOG = (OUT / "progress.log").open("w", encoding="utf-8")

SAMPLE_DATES = ["2021-06-30", "2022-06-30", "2023-06-30", "2024-06-28", "2025-06-30", "2026-06-30"]
CUT_HEIGHT = 0.3          # 距离阈值：1−ρ > 0.3 即 ρ < 0.7 分到不同组


def log(msg: str) -> None:
    line = f"[{pd.Timestamp.now().strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    LOG.write(line + "\n")
    LOG.flush()


def fetch(sql: str):
    r = requests.get("http://127.0.0.1:9000/exec", params={"query": sql}, timeout=120).json()
    if "error" in r:
        raise RuntimeError(r["error"])
    return r.get("dataset") or []


def stored_series(factor_id: str, date: str) -> pd.Series:
    rows = fetch(
        "select code, last(value) value from factor_value_daily "
        f"where factor_id='{factor_id}' and ts <= '{date}' group by code"
    )
    if not rows:
        return pd.Series(dtype="float64")
    idx, vals = [], []
    for code, value in rows:
        idx.append(str(code))
        vals.append(float(value) if value is not None else np.nan)
    out = pd.Series(vals, index=idx)
    return out.groupby(level=0).last()


def main() -> int:
    stored_ids = sorted({str(r[0]) for r in fetch(
        "select distinct factor_id from factor_value_daily")})
    online_ids = sorted(set(PRICE_NAMES) | set(FLOW_FACTORS) | set(DELTA_FACTORS) | set(LOWFREQ_FACTORS))
    log(f"在线 {len(online_ids)} + 预计算 {len(stored_ids)} = {len(online_ids) + len(stored_ids)}")

    cache = OUT / "corr_matrix.csv"
    if cache.exists() and len(sys.argv) > 1 and sys.argv[1] == "--from-cache":
        log("读取缓存的相关系数矩阵")
        return cluster_from(pd.read_csv(cache, index_col=0))

    log("加载面板…")
    panel = load_panel(None, None, "2019-01-01", None, universe="all_a",
                       extras=("fin_valuation_daily", "margin_detail", "moneyflow_daily"))
    dates = [pd.Timestamp(d) for d in SAMPLE_DATES]
    snapped = []
    for d in dates:
        pos = panel.close.index.searchsorted(d)
        if pos < len(panel.close.index):
            snapped.append(panel.close.index[pos])
    dates = sorted(set(snapped))
    log(f"采样日 {len(dates)} 个：{[str(d.date()) for d in dates]}")

    series: dict[str, np.ndarray] = {}
    cols = panel.close.columns

    for i, fid in enumerate(online_ids, 1):
        try:
            if fid.startswith("sentiment."):
                f = flow_factor_panel(fid, panel)
            else:
                f = price_factor_panel(fid, panel)
        except Exception as exc:  # noqa: BLE001
            log(f"  跳过 {fid}: {type(exc).__name__}: {str(exc)[:40]}")
            continue
        block = []
        for d in dates:
            row = f.loc[d].reindex(cols).to_numpy(dtype="float64")
            block.append(pd.Series(row).rank(pct=True).to_numpy())
        series[fid] = np.concatenate(block)
        if i % 20 == 0:
            log(f"  在线因子 {i}/{len(online_ids)}")

    for i, fid in enumerate(stored_ids, 1):
        block = []
        for d in dates:
            s = stored_series(fid, str(d.date()))
            row = s.reindex(cols).to_numpy(dtype="float64")
            block.append(pd.Series(row).rank(pct=True).to_numpy())
        arr = np.concatenate(block)
        if np.isfinite(arr).sum() > 1000:
            series[fid] = arr
        if i % 20 == 0:
            log(f"  预计算因子 {i}/{len(stored_ids)}")

    ids = sorted(series)
    log(f"有效因子 {len(ids)}，开始算相关矩阵")
    mat = pd.DataFrame({k: series[k] for k in ids})
    corr = mat.corr(method="pearson", min_periods=500)
    corr.to_csv(OUT / "corr_matrix.csv", encoding="utf-8-sig")
    log("相关矩阵已保存")

    groups = _union_find_groups(corr, threshold=1.0 - CUT_HEIGHT)
    ids = [c for members in groups.values() for c in members]

    lines = ["# 因子聚类结果\n", f"阈值：距离 {CUT_HEIGHT}（ρ < {1 - CUT_HEIGHT:.1f} 分到不同组）\n",
             f"因子数 {len(ids)} → 组数 {len(groups)}\n"]
    for rank, (gid, members) in enumerate(groups.items(), 1):
        sub = corr.loc[members, members].values
        off = sub[~np.eye(len(members), dtype=bool)]
        avg = float(np.nanmean(off)) if off.size else float("nan")
        rep = members[0]
        lines.append(f"\n## 组 {rank}（{len(members)} 个，组内平均 ρ={avg:.2f}）\n")
        lines.append("代表：`" + rep + "`\n\n")
        lines.append(", ".join(f"`{m}`" for m in members) + "\n")
    (OUT / "groups.md").write_text("".join(lines), encoding="utf-8")
    (OUT / "groups.json").write_text(json.dumps(
        {str(k): v for k, v in groups.items()}, ensure_ascii=False, indent=2), encoding="utf-8")

    log(f"分组完成：{len(groups)} 组")
    for gid, members in groups.items():
        log(f"  组{gid}: {len(members)} 个 → {members[0]}")
    log("DONE")
    LOG.close()
    return 0



def _union_find_groups(corr: pd.DataFrame, threshold: float = 0.7) -> dict[int, list[str]]:
    """并查集聚类：相关系数 >= threshold 的因子合并成一组（连通分量）。"""
    ids = list(corr.index)
    parent = {i: i for i in ids}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            v = corr.at[a, b]
            if pd.notna(v) and v >= threshold:
                union(a, b)

    buckets: dict[str, list[str]] = {}
    for i in ids:
        buckets.setdefault(find(i), []).append(i)
    ordered = sorted(buckets.values(), key=lambda m: -len(m))
    return {i + 1: m for i, m in enumerate(ordered)}


def cluster_from(corr: pd.DataFrame) -> int:
    """从已有相关矩阵直接聚类（秒级）。"""
    groups = _union_find_groups(corr, threshold=1.0 - CUT_HEIGHT)
    lines = ["# 因子聚类结果\n", f"阈值：ρ ≥ {1 - CUT_HEIGHT:.1f} 合并同组\n",
             f"因子数 {len(corr)} → 组数 {len(groups)}\n"]
    for rank, members in groups.items():
        sub = corr.loc[members, members].values
        off = sub[~np.eye(len(members), dtype=bool)]
        avg = float(np.nanmean(off)) if off.size else float("nan")
        central = corr.loc[members, members].mean().idxmax()
        lines.append(f"\n## 组 {rank}（{len(members)} 个，组内平均 ρ={avg:.2f}）\n")
        lines.append(f"代表：`{central}`\n\n")
        lines.append(", ".join(f"`{m}`" for m in members) + "\n")
        log(f"  组{rank}: {len(members)} 个 | 代表 {central} | 组内平均 ρ={avg:.2f}")
    (OUT / "groups.md").write_text("".join(lines), encoding="utf-8")
    (OUT / "groups.json").write_text(json.dumps(
        {str(k): v for k, v in groups.items()}, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"分组完成：{len(groups)} 组 → {OUT / 'groups.md'}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
