"""Squeeze Momentum 指标演示：飞龙股份 002536（2024-01 ~ 2026-11-03）。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

import matplotlib
matplotlib.use("Agg")

import matplotlib.font_manager as fm
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import psycopg2

from factors.technical.squeeze import squeeze_momentum

for name in ["Microsoft YaHei", "SimHei", "DengXian"]:
    if any(name.lower() in f.name.lower() for f in fm.fontManager.ttflist):
        plt.rcParams["font.sans-serif"] = [name]
        break
plt.rcParams["axes.unicode_minus"] = False

import argparse

parser = argparse.ArgumentParser(description="Squeeze Momentum 指标演示")
parser.add_argument("--code", default="002536", help="6 位股票代码")
parser.add_argument("--start", default="2024-01-01")
parser.add_argument("--end", default="2026-11-03")
args = parser.parse_args()

CODE = args.code
conn = psycopg2.connect(host="127.0.0.1", port=8812, user="admin", password="quest", dbname="stocks")
df = pd.read_sql(
    """SELECT ts, open, high, low, close, adj_factor FROM ohlcv_1d
       WHERE code=%s AND ts >= %s AND ts <= %s ORDER BY ts""",
    conn,
    params=(CODE, args.start, args.end),
)
df["ts"] = pd.to_datetime(df.ts)
df = df.set_index("ts").sort_index()
adj = df["adj_factor"] / df["adj_factor"].iloc[-1]
for col in ["open", "high", "low", "close"]:
    df[col] = df[col] * adj

res = squeeze_momentum(df["high"], df["low"], df["close"])
out = res.to_frame()
out.insert(0, "close", df["close"].to_numpy())

print("bars:", len(out), out.index.min().date(), "->", out.index.max().date())
print(out.tail(12).round(3).to_string())

last = out.iloc[-1]
state = "SQUEEZE ON" if last.sqz_on else ("SQUEEZE OFF" if last.sqz_off else "NO SQUEEZE")
print(
    f"\n最新 {out.index[-1].date()} close={last.close:.2f} val={last.val:.3f} "
    f"state={state} bar={last.bar_color} dot={last.dot_color}"
)

# 最近一次 squeeze on 的起点
on = out.sqz_on.fillna(False)
if on.any():
    blocks = (on != on.shift(1)).cumsum()[on]
    last_block = blocks.iloc[-1]
    start = blocks[blocks == last_block].index[0]
    print("最近一段 squeeze on:", start.date(), "->", blocks[blocks == last_block].index[-1].date())

# ---------- plot ----------
x = np.arange(len(out))
dates = out.index
fig, (ax, ax2) = plt.subplots(
    2, 1, figsize=(13.5, 8.6), sharex=True,
    gridspec_kw={"height_ratios": [1.55, 1], "hspace": 0.08},
)

# squeeze 区间底色
blocks = (on != on.shift(1)).cumsum()
for block_id, group in out.groupby(blocks):
    if bool(group.sqz_on.iloc[0]):
        ax.axvspan(group.index[0], group.index[-1], color="#eceff1", alpha=0.75, zorder=0)
        ax2.axvspan(group.index[0], group.index[-1], color="#eceff1", alpha=0.75, zorder=0)

ax.plot(dates, out.close, color="#1f2937", lw=1.3, label="Close", zorder=5)
ax.plot(dates, out.upper_bb, color="#2962ff", lw=0.9, alpha=0.85, label="BB Upper/Lower", zorder=3)
ax.plot(dates, out.lower_bb, color="#2962ff", lw=0.9, alpha=0.85, zorder=3)
ax.plot(dates, out.upper_kc, color="#f59e0b", lw=0.9, ls="--", alpha=0.9, label="KC Upper/Lower", zorder=3)
ax.plot(dates, out.lower_kc, color="#f59e0b", lw=0.9, ls="--", alpha=0.9, zorder=3)
ax.fill_between(dates, out.lower_bb, out.upper_bb, color="#2962ff", alpha=0.06, zorder=1)

ax.set_title(
    f"飞龙股份 {CODE} · Squeeze Momentum (LazyBear) · 2024-01 ~ 2026-11-03",
    fontsize=14, pad=10,
)
ax.set_ylabel("价格（前复权）")
ax.legend(loc="upper left", fontsize=8, ncol=3, framealpha=0.9)
ax.grid(alpha=0.2, ls=":")
ax.annotate(
    f"{last.close:.2f}",
    xy=(dates[-1], last.close), xytext=(6, 0), textcoords="offset points",
    fontsize=8, color="#1f2937", va="center",
)

colors = out.bar_color.fillna("#cccccc").tolist()
ax2.vlines(dates, 0, out.val, colors=colors, lw=1.3, zorder=2)
ax2.axhline(0, color="#78909c", lw=0.8, zorder=3)
dot_colors = out.dot_color.fillna("#cccccc").tolist()
ax2.scatter(dates, np.zeros(len(dates)), c=dot_colors, s=10, marker="o", edgecolors="none", zorder=4)
ax2.set_ylabel("Momentum (val)")
ax2.grid(alpha=0.2, ls=":")

# 图例说明
from matplotlib.lines import Line2D
legend_items = [
    Line2D([0], [0], marker="o", color="w", markerfacecolor="#00ff00", markersize=7, label="正值走强 (lime)"),
    Line2D([0], [0], marker="o", color="w", markerfacecolor="#008000", markersize=7, label="正值走弱 (green)"),
    Line2D([0], [0], marker="o", color="w", markerfacecolor="#ff0000", markersize=7, label="负值走弱 (red)"),
    Line2D([0], [0], marker="o", color="w", markerfacecolor="#800000", markersize=7, label="负值走强 (maroon)"),
    Line2D([0], [0], marker="o", color="w", markerfacecolor="#0000ff", markersize=7, label="无挤压 (blue)"),
    Line2D([0], [0], marker="o", color="w", markerfacecolor="#000000", markersize=7, label="挤压中 (black)"),
    Line2D([0], [0], marker="o", color="w", markerfacecolor="#808080", markersize=7, label="挤压释放 (gray)"),
]
ax2.legend(handles=legend_items, loc="upper left", fontsize=7.5, ncol=4, framealpha=0.9)

ax.set_xlim(dates[0], dates[-1] + pd.Timedelta(days=10))
plt.savefig(f"output/squeeze/{CODE}_squeeze.png", dpi=160, bbox_inches="tight", facecolor="white")
print(f"\nsaved output/squeeze/{CODE}_squeeze.png")
